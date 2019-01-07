import random
import time
from copy import copy
from datetime import datetime, timedelta
from random import randrange
from typing import Dict, Iterable, List, Optional, Set
from uuid import uuid4

from django.conf import settings
from hearthstone.enums import CardClass, FormatType
from redis import StrictRedis

from hsreplaynet.decks.models import ClusterSnapshot
from hsreplaynet.utils.redis import (
	SECONDS_PER_DAY, RedisIntegerMapStorage, RedisPopularityDistribution, RedisTree
)


def _get_random_cache(available_caches, name):
	available_replicas = [c for c in settings.CACHES if name in c]
	return available_caches[random.choice(available_replicas)]


def deck_prediction_tree(player_class, game_format, redis_client=None):
	from django.core.cache import caches

	player_class = CardClass(int(player_class))
	game_format = FormatType(int(game_format))
	if redis_client is None:
		redis_primary = caches["deck_prediction_primary"]
		redis_replica = _get_random_cache(caches, "deck_prediction_replica") or redis_primary
		redis_primary = redis_primary.client.get_client()
		redis_replica = redis_replica.client.get_client()
	else:
		redis_primary = redis_client
		redis_replica = redis_primary

	return DeckPredictionTree(
		player_class, game_format, redis_primary, redis_replica
	)


class PredictionResult:
	def __init__(self, tree, predicted_deck_id, node, tie, match_attempts, sequence):
		self.tree = tree
		self.predicted_deck_id = predicted_deck_id
		self.node = node
		self.tie = tie
		self.match_attempts = match_attempts
		self.play_sequences = sequence
		if node:
			self.popularity_distribution = tree._popularity_distribution(node)
		else:
			self.popularity_distribution = None

	def path(self):
		stack = []
		node = self.node
		while node:
			stack.insert(0, node.label)
			node = node.parent
		return stack


DEFAULT_POPULARITY_TTL = 2 * SECONDS_PER_DAY


class DeckPredictionTree:
	def __init__(
		self, player_class, format, redis_primary, redis_replica,
		max_depth=4,
		ttl=DEFAULT_POPULARITY_TTL,
		popularity_ttl=DEFAULT_POPULARITY_TTL,
		include_current_bucket=settings.INCLUDE_CURRENT_BUCKET_IN_LOOKUP,
		bucket_size=21600  # 6 Hours
	):
		self.redis_primary = redis_primary
		self.redis_replica = redis_replica
		self.player_class = player_class
		self.format = format
		self.max_depth = max_depth
		self.ttl = ttl
		self.popularity_ttl = popularity_ttl
		self.include_current_bucket = include_current_bucket
		self.bucket_size = bucket_size
		self.storage = RedisIntegerMapStorage(
			(redis_primary, redis_replica), "DECK", ttl=self.ttl
		)
		self.tree_name = "%s_%s_%s" % ("DECK_PREDICTION", player_class.name, format.name)
		self.tree = RedisTree(self.redis_primary, self.tree_name, ttl=self.ttl)

	def lookup(self, dbf_map, sequence):
		play_sequence = copy(sequence)
		predicted_deck_id, node, tie, match_attempts = self._lookup(dbf_map, play_sequence)
		return PredictionResult(
			self,
			predicted_deck_id,
			node,
			tie,
			match_attempts,
			sequence
		)

	def _lookup(self, dbf_map, play_sequence):
		# Seek to the maximum depth in the tree
		stack = []
		node = self.tree.root

		while node and len(play_sequence):
			stack.insert(0, node)
			next_sequence = play_sequence.pop(0)
			next_node = node.get_child(next_sequence, create=False)
			if next_node:
				node = next_node
		stack.insert(0, node)

		# Then start looking for a match starting from the deepest node
		match_attempts = 0
		while len(stack):
			node = stack.pop(0)
			popularity_dist = self._popularity_distribution(node)

			if self.include_current_bucket:
				end_ts = datetime.utcnow()
			else:
				end_ts = datetime.utcnow() - timedelta(seconds=self.bucket_size)

			# Do not request more candidates than the maximum amount
			# That our deck storage system supports brute force search over
			# - 1 because distribution(limit=..) is inclusive
			num_candidates = self.storage.max_match_size - 1
			candidate_decks = popularity_dist.distribution(
				end_ts=end_ts,
				limit=num_candidates
			)

			sorted_candidates = sorted(
				candidate_decks.items(),
				key=lambda t: t[1],
				reverse=True
			)
			most_popular = [k for k, v in sorted_candidates]

			matches = self.storage.match(dbf_map, *most_popular)
			match_attempts += 1

			if len(matches) > 1:
				first_match = matches[0]
				first_match_popularity = candidate_decks[first_match]
				second_match = matches[1]
				second_match_popularity = candidate_decks[second_match]

				# If multiple matches have equal popularity then return None
				if first_match_popularity > second_match_popularity:
					return int(first_match), node, False, match_attempts
				else:
					# There is a tie for most popular deck
					if node.depth == 0:
						# We are at the root so we must make a choice.
						top_matches = [first_match, second_match]
						# Get all the decks tied for first place
						for additional_match in matches[2:]:
							if candidate_decks[additional_match] == first_match_popularity:
								top_matches.append(additional_match)
						final_match = top_matches[randrange(0, len(top_matches))]
						return int(final_match), node, False, match_attempts
					else:
						pass
						# We are not at the root, so we pass
						# And let a node higher up the tree decide

			if len(matches) == 1:
				return int(matches[0]), node, False, match_attempts

		return None, None, False, match_attempts

	def observe(self, deck_id, dbf_map, play_sequence, as_of=None):
		self.storage.store(deck_id, dbf_map)
		return self._observe(deck_id, copy(play_sequence), as_of)

	def _observe(self, deck_id, play_sequence, as_of=None):
		node = self.tree.root
		while node and node.depth < self.max_depth:
			popularity_dist = self._popularity_distribution(node)
			popularity_dist.increment(deck_id, as_of=as_of)
			if len(play_sequence):
				next_sequence = play_sequence.pop(0)
				node = node.get_child(next_sequence, create=True)
			else:
				break

	def _popularity_distribution(self, node):
		dist = RedisPopularityDistribution(
			self.redis_primary,
			name=node.key,
			namespace="POPULARITY",
			ttl=self.popularity_ttl,
			max_items=self._max_collection_size_for_depth(node.depth),
			bucket_size=self.bucket_size
		)
		return dist

	def _max_collection_size_for_depth(self, depth, min_size=1000.0):
		# Nodes closer to the root retain more deck state
		# Since a greater percentage of the global deck volume flows through them
		# 0 -> min_size * (16 / 2 ** 0) = 16 * min_size
		# 1 -> min_size * (16 / 2 ** 0) = 16 * min_size
		# 2 -> min_size * (16 / 2 ** 1) = 8 * min_size
		# 3 -> min_size * (16 / 2 ** 1) = 8 * min_size
		# 4 -> min_size * (16 / 2 ** 2) = 4 * min_size
		# 5 -> min_size * (16 / 2 ** 2) = 4 * min_size
		# 6 -> min_size * (16 / 2 ** 3) = 2 * min_size
		# 7 -> min_size * (16 / 2 ** 3) = 2 * min_size
		# 8 -> min_size * (16 / 2 ** 4) = 1 * min_size
		# 9+ = min_size
		# from math import ceil, floor, pow
		# return int(min_size * ceil(16.0 / pow(2.0, floor(depth / 2.0))))
		return 20000

	def display(self, out):
		from hsreplaynet.utils import card_db
		db = card_db()
		out.write("\n** %s - %s **" % (self.player_class.name, self.format.name))

		discovered = set()
		stack = [self.tree.root]
		while len(stack):
			v = stack.pop(0)
			if str(v) not in discovered:
				discovered.add(str(v))
				if v.label == "ROOT":
					card_name = "ROOT"
				else:
					card_name = db[int(v.label)].name

				dist = self._popularity_distribution(v).distribution()
				size = len(dist)
				if size:
					observations = sum(dist.values())
					vals = ("\t" * v.depth, v.depth, card_name, size, observations)
					out.write(
						"%s(%i) %s (%i Decks, %i Observations):" % vals
					)

				for child in v.children():
					stack.insert(0, child)


class BaseInverseLookupTable:
	"""
	This class specificies the API for an Inverse Lookup Table (ILT) to be used for
	deck prediction.
	"""

	def observe(
		self, dbf_map: Dict[int, int], deck_id: int, uuid: Optional[str] = None
	) -> None:
		"""
		This method takes a list of cards as a dict of {card_id: count} items and stores an
		entry pointing to the deck_id for each of the cards. You may optionally pass a UUID
		(such as a replay shortid) in order to deduplicate observations.
		"""
		raise NotImplementedError

	def predict(self, dbf_map: Dict[int, int]) -> Optional[int]:
		"""
		This method takes a list of cards as a dict of {card_id: count} items and attempts
		to find the most likely deck_id. If none is found, this method will return None.
		"""
		raise NotImplementedError


class RedisInverseLookupTable(BaseInverseLookupTable):
	def __init__(
		self,
		redis: StrictRedis,
		game_format: FormatType,
		player_class: CardClass,
		required_cards: Optional[Set[int]] = None,
		min_cards_for_prediction: int = settings.ILT_DECK_PREDICTION_MINIMUM_CARDS,
		ilt_lookback_mins: int = settings.ILT_LOOKBACK_MINS,
		deck_popularity_lookback_mins: int = settings.ILT_DECK_POPULARITY_LOOKBACK_MINS,
		full_deck_size: int = 30,
	) -> None:
		self.redis = redis
		self.game_format = game_format
		self.player_class = player_class
		self.required_cards = required_cards or set()
		self.ilt_lookback_mins = ilt_lookback_mins
		self.deck_popularity_lookback_mins = deck_popularity_lookback_mins
		self.min_cards_for_prediction = min_cards_for_prediction
		self.full_deck_size = full_deck_size

	@property
	def namespace(self):
		return f"DECK_PREDICTION_ILT:{self.game_format.name}_{self.player_class.name}"

	def observe(
		self, dbf_map: Dict[int, int], deck_id: int, uuid: Optional[str] = None
	) -> None:
		if not self._is_full_deck(dbf_map):
			raise ValueError("The deck is not a full deck")

		# grab the current time
		now = int(time.time() * 1000)

		# pipeline all the following commands
		pipeline = self.redis.pipeline()

		# observe the cards in ILTs
		for key in self._get_card_keys(dbf_map):
			pipeline.zadd(key, now, deck_id)
			pipeline.expire(key, self.ilt_lookback_mins * 60)

		# observe the deck for popularity
		deck_key = self._get_deck_key(deck_id)
		uuid = uuid or str(uuid4())
		pipeline.zadd(deck_key, now, uuid)
		pipeline.expire(deck_key, self.deck_popularity_lookback_mins * 60)

		# send the pipeline over the wire
		pipeline.execute()

	def _is_full_deck(self, dbf_map: Dict[int, int]) -> bool:
		return sum(count for card_id, count in dbf_map.items()) == self.full_deck_size

	def _get_card_keys(self, dbf_map: Dict[int, int]) -> Set[str]:
		keys = set()
		for card_id, count in dbf_map.items():
			# Also return all permutations with fewer cards, as if a card is in a deck twice
			# we also want to be able to predict the deck if we only observe it once.
			for i in range(1, count + 1):
				keys.add(self._get_card_key(card_id, i))
		return keys

	def _get_card_key(self, card_id: int, count: int = 1) -> str:
		if not isinstance(card_id, int):
			raise ValueError("Expected dbf id as card id")
		return f"{self.namespace}:ILT:{int(card_id)}:x{int(count)}"

	def _get_deck_key(self, deck_id: int) -> str:
		return f"{self.namespace}:POPULARITY:{deck_id}"

	def predict(self, dbf_map: Dict[int, int]) -> Optional[int]:
		# check to see whether enough the deck to predict has enough cards
		if not self._is_predictable_deck(dbf_map):
			return None

		# get the associated redis keys
		keys = self._get_card_keys(dbf_map)

		# check for expiry first
		now_msecs = int(time.time() * 1000)
		lookback_msecs = self.ilt_lookback_mins * 60 * 1000
		pipeline = self.redis.pipeline()
		for key in keys:
			pipeline.zremrangebyscore(key, 0, now_msecs - lookback_msecs)
		pipeline.execute()

		# trivial case: find the decks from a intersection over all cards
		candidates = self._zinter(keys)
		if len(candidates):
			deck_ids = map(lambda deck_id: int(deck_id.decode("utf-8")), candidates)
			return max(deck_ids, key=lambda deck_id: self._get_popularity_for_deck(deck_id))

		# count number of decks for each card once before fuzzy matching
		sorted_keys = sorted(keys, key=lambda key: self.redis.zcard(key))
		required_keys = self._get_card_keys({key: 1 for key in self.required_cards})

		# fuzzy matching: as long as we can safely remove one card...
		while len(sorted_keys) > self.min_cards_for_prediction:
			# ...find a non-required card to remove
			for index, key_to_remove in enumerate(sorted_keys):
				if key_to_remove not in required_keys:
					del sorted_keys[index]
					break
			else:
				# we were unable to remove anything, terminate
				return None
			# ...calculate our new candidates
			candidates = self._zinter(sorted_keys)
			# ...and finally check whether we got a match
			if len(candidates) > 0:
				deck_ids = map(lambda deck_id: int(deck_id.decode("utf-8")), candidates)
				return max(deck_ids, key=lambda deck_id: self._get_popularity_for_deck(deck_id))

		# we were unable to match a deck
		return None

	def _zinter(self, keys: Iterable[str]) -> List[str]:
		tmp_key = f"{self.namespace}:INTERSECT:{uuid4()}"
		pipeline = self.redis.pipeline()
		pipeline.zinterstore(tmp_key, keys=keys)
		pipeline.zrange(tmp_key, 0, -1)
		pipeline.delete(tmp_key)
		results = pipeline.execute()
		return results[1]

	def _is_predictable_deck(self, dbf_map: Dict[int, int]) -> bool:
		num_cards = 0
		for card_id, count in dbf_map.items():
			num_cards = num_cards + count
			if num_cards >= self.min_cards_for_prediction:
				# we've seen enough, terminate early
				return True
		else:
			# if the loop exits normally we haven't seen enough cards
			return False

	def _get_popularity_for_deck(self, deck_id: int) -> int:
		key = self._get_deck_key(deck_id)
		now_msecs = int(time.time() * 1000)
		lookback_msecs = self.deck_popularity_lookback_mins * 60 * 1000
		# remove all observations with a "timestamp" (score) that is too old (low)
		self.redis.zremrangebyscore(key, 0, now_msecs - lookback_msecs)
		# count all remaining observations (0, -1 do not work here and we need -/+inf)
		return self.redis.zcount(key, "-inf", "+inf")


def inverse_lookup_table(
	game_format: FormatType, player_class: CardClass, redis_client=None
) -> BaseInverseLookupTable:
	if not redis_client:
		from django.core.cache import caches
		redis_client = caches["ilt_deck_prediction"]

	required_cards = ClusterSnapshot.objects.get_required_cards_for_player_class(
		game_format, player_class
	)

	return RedisInverseLookupTable(redis_client, game_format, player_class, required_cards)
