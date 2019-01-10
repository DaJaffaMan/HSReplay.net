import collections
import hashlib
import json
import os
import string
import time
from typing import Set

from django.conf import settings
from django.contrib.postgres.fields import ArrayField, JSONField
from django.db import connection, models, transaction
from django.dispatch.dispatcher import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import now
from django_hearthstone.cards.models import Card
from django_intenum import IntEnumField
from hearthstone import deckstrings, enums
from hsarchetypes import classify_deck
from hsarchetypes.clustering import ClassClusters, Cluster, ClusterSet, create_cluster_set
from shortuuid.main import int_to_string, string_to_int

from hsreplaynet.utils import card_db, log
from hsreplaynet.utils.aws import s3_object_exists
from hsreplaynet.utils.aws.clients import FIREHOSE, LAMBDA, S3
from hsreplaynet.utils.aws.redshift import get_redshift_query
from hsreplaynet.utils.aws.streams import (
	publish_batch_to_firehose, publish_from_iterable_at_fixed_speed, to_data_blobs
)
from hsreplaynet.utils.db import dictfetchall
from hsreplaynet.utils.influx import influx_metric, influx_timer


ALPHABET = string.ascii_letters + string.digits


class DeckManager(models.Manager):
	def get_or_create_from_id_list(
		self,
		id_list,
		hero_id=None,
		game_type=None,
		classify_archetype=False
	):
		deck, created = self._get_or_create_deck_from_db(id_list)

		if deck.size is None:
			deck.size = len(deck.card_id_list())
			deck.save()

		if deck.archetype_id and not Archetype.objects.filter(id=deck.archetype_id).exists():
			influx_metric(
				"deleted_archetype_cleanup",
				{
					"count": 1,
				},
			)
			deck.update_archetype(None)

		if not deck.is_full_deck and deck.archetype_id is not None:
			# Earlier implementations of archetype assignment classified partial decks
			# This creates an online cleanup of those archetype assignments
			# This block can be removed once the cleanup metric is near enough to zero
			influx_metric(
				"partial_deck_archetype_cleanup",
				{
					"count": 1,
				},
			)
			deck.update_archetype(None)

		archetypes_enabled = settings.ARCHETYPE_CLASSIFICATION_ENABLED
		archetype_missing = deck.archetype_id is None
		full_deck = deck.is_full_deck
		eligible_game_type = not game_type or game_type != enums.BnetGameType.BGT_ARENA
		if (
			archetypes_enabled and
			classify_archetype and
			archetype_missing and
			full_deck and
			eligible_game_type
		):
			player_class = self._convert_hero_id_to_player_class(hero_id)
			picked_class = self._pick_card_class(player_class, deck.deck_class)
			deck.classify_into_archetype(picked_class)

		return deck, created

	def _get_or_create_deck_from_db(self, id_list):
		if not id_list:
			# Empty list; not supported by our db function
			digest = generate_digest_from_deck_list(id_list)
			return Deck.objects.get_or_create(digest=digest)

		# This native implementation in the DB is to reduce the volume
		# of DB chatter between Lambdas and the DB
		cursor = connection.cursor()
		cursor.callproc("get_or_create_deck", (id_list, ))
		result_row = cursor.fetchone()
		deck_id = int(result_row[0])
		created_ts, digest, created, deck_size = result_row[1:]
		cursor.close()
		d = Deck.objects.get(id=deck_id)
		return d, created

	def _pick_card_class(self, player_class, deck_class):
		if player_class == deck_class:
			return player_class

		# never pick an INVALID hero
		if player_class == enums.CardClass.INVALID:
			return deck_class

		# don't pick mixed or neutral-only decks
		if deck_class in (enums.CardClass.INVALID, enums.CardClass.NEUTRAL):
			return player_class

		# finally, fall back to deck
		return deck_class

	def _convert_hero_id_to_player_class(self, hero_id):
		if isinstance(hero_id, int):
			return Card.objects.get(dbf_id=hero_id).card_class
		elif hero_id:
			return Card.objects.get(card_id=hero_id).card_class
		return enums.CardClass.INVALID

	def bulk_update_to_archetype(self, deck_ids, archetype):
		if isinstance(archetype, Archetype):
			archetype_id = archetype.id
		else:
			archetype_id = archetype

		timestamp = now().replace(tzinfo=None)

		records = []
		for deck_id in deck_ids:
			records.append("{deck_id}|{archetype_id}|{as_of}\n".format(
				deck_id=str(deck_id),
				archetype_id=str(archetype_id or ""),
				as_of=timestamp.isoformat(sep=" "),
			))

		bulk_records = to_data_blobs(records)
		if len(bulk_records):
			publish_from_iterable_at_fixed_speed(
				iter(bulk_records),
				self._publish_archetypes_to_firehose,
				max_records_per_second=5000,
				publish_batch_size=500
			)

		Deck.objects.filter(id__in=deck_ids).update(archetype_id=archetype_id)

	def _publish_archetypes_to_firehose(self, batch):
		publish_batch_to_firehose(settings.ARCHETYPE_FIREHOSE_STREAM_NAME, batch)

	def get_digest_from_shortid(self, shortid):
		try:
			id = string_to_int(shortid, ALPHABET)
		except ValueError:
			raise Deck.DoesNotExist("Invalid deck ID")
		digest = hex(id)[2:].rjust(32, "0")
		return digest

	def get_by_shortid(self, shortid):
		return Deck.objects.get(
			digest=self.get_digest_from_shortid(shortid)
		)


def generate_digest_from_deck_list(id_list):
	sorted_cards = sorted(id_list)
	m = hashlib.md5()
	m.update(",".join(sorted_cards).encode("utf-8"))
	return m.hexdigest()


class Deck(models.Model):
	"""
	Represents an abstract collection of cards.

	The default sorting for cards when iterating over a deck is by
	mana cost and then alphabetical within cards of equal cost.
	"""

	id = models.BigAutoField(primary_key=True)
	objects = DeckManager()
	cards = models.ManyToManyField(Card, through="Include")
	digest = models.CharField(max_length=32, unique=True)
	created = models.DateTimeField(auto_now_add=True, null=True, blank=True)
	archetype = models.ForeignKey(
		"decks.Archetype", on_delete=models.SET_NULL, blank=True, null=True
	)
	size = models.IntegerField(null=True)
	guessed_full_deck = models.ForeignKey(
		"decks.Deck", on_delete=models.PROTECT, null=True, blank=True
	)

	class Meta:
		db_table = "cards_deck"

	def __str__(self):
		if self.archetype:
			return str(self.archetype)
		if self.deck_class:
			return "%s Deck" % (self.deck_class.name.capitalize())
		return "Neutral Deck"

	def __repr__(self):
		values = self.includes.values("card__name", "count", "card__cost")
		alpha_sorted = sorted(values, key=lambda t: t["card__name"])
		mana_sorted = sorted(alpha_sorted, key=lambda t: t["card__cost"])
		value_map = ["%s x %i" % (c["card__name"], c["count"]) for c in mana_sorted]
		return "[%s]" % (", ".join(value_map))

	def __iter__(self):
		# sorted() is stable, so sort alphabetically first and then by mana cost
		alpha_sorted = sorted(self.cards.all(), key=lambda c: c.name)
		mana_sorted = sorted(alpha_sorted, key=lambda c: c.cost)
		return mana_sorted.__iter__()

	@cached_property
	def hero(self):
		deck_class = self.deck_class
		if deck_class and deck_class != enums.CardClass.NEUTRAL:
			return deck_class.default_hero

	@property
	def hero_dbf_id(self):
		if not self.hero:
			return None
		return Card.objects.get(card_id=self.hero).dbf_id

	@cached_property
	def deck_class(self):
		has_seen_neutral = False
		primary_card_class = None
		for val in self.includes.values("card__card_class"):
			card_class = val["card__card_class"]
			if card_class == enums.CardClass.NEUTRAL:
				has_seen_neutral = True
			if card_class not in (enums.CardClass.INVALID, enums.CardClass.NEUTRAL):
				if primary_card_class and card_class != primary_card_class:
					return enums.CardClass.INVALID
				primary_card_class = card_class
		if primary_card_class:
			return primary_card_class
		elif has_seen_neutral:
			return enums.CardClass.NEUTRAL
		else:
			return enums.CardClass.INVALID

	@cached_property
	def card_dbf_id_packed_list(self):
		cardlist = self.card_dbf_id_list()
		sorted_list = sorted(set(cardlist))
		return [(id, cardlist.count(id)) for id in sorted_list]

	@cached_property
	def deckstring(self):
		cards = self.card_dbf_id_packed_list
		if not self.hero_dbf_id:
			return None
		return deckstrings.write_deckstring(cards, [self.hero_dbf_id], self.format)

	@cached_property
	def format(self):
		for val in self.includes.values("card__card_set"):
			card_set = val["card__card_set"]
			is_classic = card_set in (enums.CardSet.EXPERT1, enums.CardSet.CORE)
			if not is_classic and not card_set.is_standard:
				return enums.FormatType.FT_WILD
		return enums.FormatType.FT_STANDARD

	@property
	def shortid(self):
		return int_to_string(int(self.digest, 16), ALPHABET)

	@property
	def all_includes(self):
		"""
		Use instead of .includes if you know you will use all of them
		this will prefetch the related cards. (eg. in a deck list)
		"""
		fields = ("id", "count", "deck_id", "card__name")
		return self.includes.all().select_related("card").only(*fields)

	@property
	def eligible_for_global_stats(self):
		from hsreplaynet.analytics.processing import _get_global_stats_eligible_decks
		return self.digest in _get_global_stats_eligible_decks()

	@property
	def is_full_deck(self):
		size = self.size
		if self.size is None:
			size = sum(i.count for i in self.includes.all())
		return size == 30

	def get_absolute_url(self):
		if not self.is_full_deck:
			return None
		return reverse("deck_detail", kwargs={"id": self.shortid})

	def update_archetype(self, archetype, save=True, force=True):
		"""
		Set the archetype field and save to the database,
		then call sync_archetype_to_firehose() to replicate the
		archetype update into Redshift.
		"""
		if isinstance(archetype, Archetype):
			archetype_id = archetype.id
		else:
			archetype_id = archetype
		if not force and self.archetype_id == archetype_id:
			return False
		self.archetype_id = archetype_id
		if save:
			self.save()
		self.sync_archetype_to_firehose()

		return True

	def sync_archetype_to_firehose(self):
		timestamp = now().replace(tzinfo=None)
		record = "{deck_id}|{archetype_id}|{as_of}\n".format(
			deck_id=str(self.id),
			archetype_id=str(self.archetype_id or ""),
			as_of=timestamp.isoformat(sep=" "),
		)

		result = FIREHOSE.put_record(
			DeliveryStreamName=settings.ARCHETYPE_FIREHOSE_STREAM_NAME,
			Record={
				"Data": record.encode("utf-8"),
			}
		)

		return result

	def card_dbf_id_list(self):
		result = []

		includes = self.includes.values_list("card__dbf_id", "count")
		for id, count in includes:
			for i in range(count):
				result.append(id)

		return result

	def dbf_map(self, transformer=int):
		includes = self.includes.values_list("card__dbf_id", "count")
		return {transformer(id): count for id, count in includes}

	def card_id_list(self):
		result = []

		includes = self.includes.values_list("card__card_id", "count")
		for id, count in includes:
			for i in range(count):
				result.append(id)

		return result

	def predicted_card_id_list(self):
		if self.guessed_full_deck and self.digest != self.guessed_full_deck.digest:
			return self.guessed_full_deck.card_id_list()

	def as_dbf_json(self, serialized=True):
		"""Serialize the deck list for storage in Redshift"""
		result = []
		for include in self.includes.all():
			result.append([include.card.dbf_id, include.count])

		if serialized:
			# separators=(",", ":") creates compact JSON encoding
			return json.dumps(result, separators=(",", ":"))
		else:
			return result

	def classify_into_archetype(self, player_class, save: bool = True) -> int:
		game_format = self.format

		signature_weights = ClusterSnapshot.objects.get_signature_weights(
			game_format, player_class
		)

		blocked_classifications = {}

		# A failure callback for classify_deck so that we can get notified when a deck can't
		# be classified to an archetype because it's missing a card or fails a rule check.
		# This callback stores the results so that we can instrument them below.

		def record_classification_failure(failure_description):
			reason = failure_description.get("reason")
			if reason:
				if reason in blocked_classifications:
					blocked_classifications[reason] = blocked_classifications[reason] + 1
				else:
					blocked_classifications[reason] = 1

		sig_archetype_id = classify_deck(
			self.dbf_map(),
			signature_weights,
			failure_callback=record_classification_failure
		)

		# Instrument any failures that occurred during the classification attempt.

		for block_reason, count in blocked_classifications.items():
			influx_metric(
				"archetype_classification_blocked", {
					"count": count
				},
				reason=block_reason
			)

		# New Style Deck Prediction
		nn_archetype_id = None
		# nn_archetype_id = ClusterSetSnapshot.objects.predict_archetype_id(
		# 	player_class,
		# 	game_format,
		# 	self,
		# )

		archetype_id = sig_archetype_id or nn_archetype_id
		influx_metric(
			"archetype_prediction_outcome",
			{
				"count": 1,
				"signature_weight_archetype_id": sig_archetype_id,
				"neural_net_archetype_id": nn_archetype_id,
				"archetype_id": archetype_id,
				"deck_id": self.id,
			},
			success=archetype_id is not None,
			signature_weight_success=sig_archetype_id is not None,
			neural_net_success=nn_archetype_id is not None,
			method_agreement=sig_archetype_id == nn_archetype_id,
			player_class=player_class.name,
			game_format=game_format.name
		)

		if archetype_id and save:
			self.update_archetype(archetype_id)

		return archetype_id


@receiver(models.signals.pre_save, sender=Deck)
def update_deck_archetype(sender, instance, **kwargs):
	if instance.id is not None:
		orig = Deck.objects.get(id=instance.id)
		if orig.archetype_id != instance.archetype_id:
			instance.sync_archetype_to_firehose()


class Include(models.Model):
	id = models.BigAutoField(primary_key=True)
	deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="includes")
	card = models.ForeignKey(
		Card, to_field="card_id", on_delete=models.PROTECT, related_name="included_in"
	)
	count = models.IntegerField(default=1)

	class Meta:
		db_table = "cards_include"
		unique_together = ("deck", "card")

	def __str__(self):
		return "%s x %s" % (self.card.name, self.count)


class ArchetypeManager(models.Manager):

	def live(self):
		return self.filter(deleted=False)

	def _list_decks_from_redshift_for_format(self, game_format):
		query = get_redshift_query("list_decks_by_win_rate")
		if game_format == enums.FormatType.FT_STANDARD:
			paramiterized_query = query.build_full_params(dict(
				TimeRange="LAST_30_DAYS",
				GameType="RANKED_STANDARD",
			))
		else:
			paramiterized_query = query.build_full_params(dict(
				TimeRange="LAST_30_DAYS",
				GameType="RANKED_WILD",
			))

		return paramiterized_query.response_payload["series"]["data"]

	def _get_deck_archetype_map_from_redshift(self, game_format):
		data = self._list_decks_from_redshift_for_format(game_format)

		deck_archetype_map = {}
		for player_class, decks in data.items():
			for deck in decks:
				deck_archetype_map[deck["digest"]] = deck["archetype_id"]

		return deck_archetype_map

	def _get_deck_observation_counts_from_redshift(self, game_format):
		data = self._list_decks_from_redshift_for_format(game_format)
		observations = {}
		for player_class, decks in data.items():
			for deck in decks:
				observations[deck["digest"]] = deck["total_games"]

		return observations


class Archetype(models.Model):
	"""
	Archetypes cluster decks with minor card variations that all share the same strategy
	into a common group.

	E.g. 'Freeze Mage', 'Miracle Rogue', 'Pirate Warrior', 'Zoolock', 'Control Priest'
	"""

	id = models.BigAutoField(primary_key=True)
	objects = ArchetypeManager()
	name = models.CharField(max_length=250, blank=True)
	player_class = IntEnumField(enum=enums.CardClass, default=enums.CardClass.INVALID)
	deleted = models.BooleanField(default=False)

	# Cards required by this archetype. This field gets transferred to the required_cards
	# field of ClusterSnapshot when a cluster is assigned an archetype.

	required_cards = models.ManyToManyField(Card, blank=True)

	class Meta:
		db_table = "cards_archetype"

	def __str__(self):
		return self.name

	@property
	def promoted_clusters(self):
		return ClusterSnapshot.objects.filter(
			class_cluster__player_class=self.player_class,
			external_id=self.id
		).exclude(
			class_cluster__cluster_set__promoted_on=None
		).order_by("-class_cluster__cluster_set__promoted_on")

	@property
	def standard_cluster(self):
		return ClusterSnapshot.objects.get_live_cluster_for_archetype(
			enums.FormatType.FT_STANDARD, self
		)

	@property
	def wild_cluster(self):
		return ClusterSnapshot.objects.get_live_cluster_for_archetype(
			enums.FormatType.FT_WILD, self
		)

	@property
	def sankey_visualization(self):
		return self.standard_cluster.sankey_visualization()

	@property
	def standard_signature(self):
		return self.get_signature(enums.FormatType.FT_STANDARD)

	@property
	def wild_signature(self):
		return self.get_signature(enums.FormatType.FT_WILD)

	@property
	def standard_ccp_signature(self):
		return self.get_signature(enums.FormatType.FT_STANDARD, True)

	@property
	def wild_ccp_signature(self):
		return self.get_signature(enums.FormatType.FT_WILD, True)

	def get_signature(self, game_format, use_ccp=False):
		cluster = self.promoted_clusters.filter(
			class_cluster__cluster_set__game_format=game_format
		).first()
		if not cluster:
			return {}
		signature = cluster.ccp_signature if use_ccp else cluster.signature
		return {
			"as_of": cluster.class_cluster.cluster_set.as_of,
			"format": int(cluster.class_cluster.cluster_set.game_format),
			"components": [(int(dbf_id), weight) for dbf_id, weight in signature.items()],
		}

	@property
	def standard_signature_pretty(self):
		cluster = self.standard_cluster
		if cluster:
			return cluster.pretty_signature_string()
		return ""

	@property
	def standard_ccp_signature_pretty(self):
		cluster = self.standard_cluster
		if cluster:
			return cluster.pretty_ccp_signature_string()
		return ""

	@property
	def wild_signature_pretty(self):
		cluster = self.wild_cluster
		if cluster:
			return cluster.pretty_signature_string()
		else:
			return ""

	@property
	def wild_signature_as_of(self):
		cluster = self.wild_cluster
		if cluster:
			return cluster.class_cluster.cluster_set.as_of

	@property
	def standard_signature_as_of(self):
		cluster = self.standard_cluster
		if cluster:
			return cluster.class_cluster.cluster_set.as_of

	def get_absolute_url(self):
		return reverse("archetype_detail", kwargs={"id": self.id, "slug": slugify(self.name)})


class ClusterSetManager(models.Manager):
	def snapshot(
		self,
		game_format=enums.FormatType.FT_STANDARD,
		num_clusters=20,
		merge_threshold=0.85,
		inherit_threshold=0.85,
		lookback=7,
		min_observations=100,
		min_pilots=10,
		experimental_threshold=.01,
		dry_run=False
	):
		from hsreplaynet.analytics.processing import get_cluster_set_data

		data = get_cluster_set_data(
			game_format=game_format,
			lookback=lookback,
			min_observations=min_observations,
			min_pilots=min_pilots
		)

		log.info("\nClustering Raw Data Volume:")
		total_data_points = 0
		total_observations = 0
		experimental_thresholds = {}
		for player_class_name, data_points in data.items():
			data_points_for_class = len(data_points)
			observations_for_class = sum(d["observations"] for d in data_points)
			total_observations += observations_for_class
			threshold_for_class = int(observations_for_class * experimental_threshold)
			experimental_thresholds[player_class_name] = threshold_for_class
			total_data_points += data_points_for_class
			log.info(
				"\t%s: %i Data Points,\t%i Observations,\tExperimental Threshold: %i" % (
					player_class_name,
					data_points_for_class,
					observations_for_class,
					threshold_for_class
				)
			)
		log.info(
			"\tTotal Data Points: %i, Observations: %i\n" % (
				total_data_points, total_observations
			)
		)

		inheritance_missed = []
		with transaction.atomic():
			cs_snapshot = create_cluster_set(
				data,
				factory=ClusterSetSnapshot,
				num_clusters=num_clusters,
				merge_similarity=merge_threshold,
				consolidate=False,
				create_experimental_cluster=False
			)

			previous_snapshot = ClusterSetSnapshot.objects.filter(
				live_in_production=True
			).first()

			cs_snapshot.consolidate_clusters(merge_threshold)

			ClusterSetSnapshot.objects.update(latest=False)
			cs_snapshot.game_format = game_format
			cs_snapshot.latest = True
			cs_snapshot.save()

			uninherited_id_set = cs_snapshot.inherit_from_previous(
				previous_snapshot,
				merge_threshold=inherit_threshold
			)

			for uninherited_id in uninherited_id_set:
				inheritance_missed.append(str(uninherited_id))

			cs_snapshot.create_experimental_clusters(
				experimental_cluster_thresholds=experimental_thresholds
			)

			if not dry_run:
				for class_cluster in cs_snapshot.class_clusters:
					class_cluster.cluster_set = cs_snapshot
					# Don't use pcp_adjustments after consolidation is complete
					# In order to leave signatures for tooltips unaffected.
					class_cluster.update_cluster_signatures(
						use_pcp_adjustment=False
					)
					class_cluster.save()

					for cluster in class_cluster.clusters:
						cluster.class_cluster = class_cluster
						cluster.save()

		if inheritance_missed:
			log.warn(
				"Inheritance missed on archetypes {}".format(
					", ".join(inheritance_missed)
				)
			)

		return cs_snapshot

	def predict_archetype_id(self, player_class, game_format, deck):
		class_cluster = ClassClusterSnapshot.objects.filter(
			player_class=player_class,
			cluster_set__live_in_production=True,
			cluster_set__game_format=game_format
		).first()
		if class_cluster:
			return class_cluster.predict_archetype_id(deck)
		else:
			return None


_TRAINING_DATA_CACHE = {}


class ClassClusterSnapshot(models.Model, ClassClusters):
	id = models.AutoField(primary_key=True)
	cluster_set = models.ForeignKey("ClusterSetSnapshot", on_delete=models.CASCADE)
	player_class = IntEnumField(enum=enums.CardClass, default=enums.CardClass.INVALID)

	def __str__(self):
		return ClassClusters.__str__(self)

	@property
	def clusters(self):
		if not hasattr(self, "_clusters"):
			self._clusters = list(self.clustersnapshot_set.all())
		return self._clusters

	@clusters.setter
	def clusters(self, clusters):
		self._clusters = clusters
		for cluster in clusters:
			cluster.class_cluster = self

	def _fetch_training_data(
		self,
		num_examples=1000000,
		max_dropped_cards=15,
		stratified=False,
		min_cards_for_determination=5
	):
		from hsarchetypes.features import to_neural_net_training_data
		key = (self.id, num_examples, max_dropped_cards, stratified, min_cards_for_determination)
		if key not in _TRAINING_DATA_CACHE:
			print("Constructing new training data: %s" % str(key))
			_TRAINING_DATA_CACHE[key] = to_neural_net_training_data(
				self,
				num_examples=num_examples,
				max_dropped_cards=max_dropped_cards,
				stratified=stratified,
				min_cards_for_determination=min_cards_for_determination
			)
		else:
			print("Serving data from cache: %s" % str(key))

		return _TRAINING_DATA_CACHE[key]

	def train_neural_network(
		self,
		num_examples=1000000,
		max_dropped_cards=15,
		stratified=False,
		min_cards_for_determination=5,
		batch_size=1000,
		num_epochs=20,
		base_layer_size=64,
		hidden_layer_size=64,
		num_hidden_layers=2,
		working_dir=None,
		upload_to_s3=False
	):
		from hsarchetypes.classification import train_neural_net
		from hsarchetypes.utils import plot_accuracy_graph, plot_loss_graph
		common_prefix_template = "%s_%i_%i_%s"
		values = (
			self.cluster_set.game_format.name,
			self.cluster_set.id,
			self.cluster_set.training_run_id,
			self.player_class.name,
		)
		common_prefix = common_prefix_template % values
		full_model_path = os.path.join(working_dir, common_prefix + "_model.h5")
		train_x, train_Y = self._fetch_training_data(
			num_examples=num_examples,
			max_dropped_cards=max_dropped_cards,
			stratified=stratified,
			min_cards_for_determination=min_cards_for_determination
		)
		print("Finished generating training data")
		history = train_neural_net(
			train_x,
			train_Y,
			full_model_path,
			batch_size=batch_size,
			num_epochs=num_epochs,
			base_layer_size=base_layer_size,
			hidden_layer_size=hidden_layer_size,
			num_hidden_layers=num_hidden_layers
		)
		accuracy = history.history["val_acc"][-1] * 100
		vals = (
			self.player_class.name,
			accuracy
		)
		print("%s accuracy: %.2f%%\n" % vals)

		loss_file_path = os.path.join(working_dir, common_prefix + "_loss.png")
		plot_loss_graph(history, self.player_class.name, loss_file_path)

		accuracy_file_path = os.path.join(working_dir, common_prefix + "_accuracy.png")
		plot_accuracy_graph(history, self.player_class.name, accuracy_file_path)

		if upload_to_s3:
			# The key structure for models in the bucket is as follows:
			# /models/<game_format>/<cluster_set_id>/<run_id>/<player_class>.h5
			# Which allows for easy listing of all the run_ids for a given snapshot

			# Within each run_id folder we expect:
			# A <player_class>.h5 file for each class
			# A summary.txt
			# A <player_class>_accuracy.png
			# A <player_class>_loss.png

			if os.path.exists(full_model_path):
				with open(full_model_path, "rb") as model:
					S3.put_object(
						Bucket=settings.KERAS_MODELS_BUCKET,
						Key=self.model_key,
						Body=model
					)

			if os.path.exists(loss_file_path):
				with open(loss_file_path, "rb") as model:
					S3.put_object(
						Bucket=settings.KERAS_MODELS_BUCKET,
						Key=self.loss_graph_key,
						Body=model
					)

			if os.path.exists(accuracy_file_path):
				with open(accuracy_file_path, "rb") as model:
					S3.put_object(
						Bucket=settings.KERAS_MODELS_BUCKET,
						Key=self.accuracy_graph_key,
						Body=model
					)

		return accuracy

	def predict_archetype_id(self, deck):
		event = self._to_prediction_event(deck)

		if settings.USE_ARCHETYPE_PREDICTION_LAMBDA or settings.ENV_AWS:
			with influx_timer("callout_to_predict_deck_archetype"):
				response = LAMBDA.invoke(
					FunctionName="predict_deck_archetype",
					InvocationType="RequestResponse",  # Synchronous invocation
					Payload=json.dumps(event),
				)
				if response["StatusCode"] == 200 and "FunctionError" not in response:
					result = json.loads(response["Payload"].read().decode("utf8"))
				else:
					raise RuntimeError(response["Payload"].read().decode("utf8"))
		else:
			from keras_handler import handler
			result = handler(event, None)

		predicted_class = result["predicted_class"]
		id_encoding = self.one_hot_external_ids(inverse=True)
		predicted_archetype_id = id_encoding[predicted_class]

		if predicted_archetype_id == -1:
			return None
		else:
			return predicted_archetype_id

	def _to_prediction_event(self, deck):
		from hsarchetypes.utils import to_prediction_vector_from_dbf_map
		prediction_vector = to_prediction_vector_from_dbf_map(deck.dbf_map())
		return {
			"model_bucket": settings.KERAS_MODELS_BUCKET,
			"model_key": self.model_key,
			"deck_vector": json.dumps(prediction_vector)
		}

	def neural_network_ready(self):
		return s3_object_exists(settings.KERAS_MODELS_BUCKET, self.model_key)

	@property
	def loss_graph_key(self):
		return self.common_key_prefix + "-loss.png"

	@property
	def accuracy_graph_key(self):
		return self.common_key_prefix + "-accuracy.png"

	@property
	def model_key(self):
		return self.common_key_prefix + ".h5"

	@property
	def common_key_prefix(self):
		return self.cluster_set.cluster_set_key_prefix + self.player_class.name


class ClusterManager(models.Manager):
	LIVE_SIGNATURES_QUERY = """
		SELECT
			c.external_id,
			c.ccp_signature,
			c.required_cards,
			c.rules
		FROM decks_clustersetsnapshot cs
		JOIN decks_classclustersnapshot ccs ON ccs.cluster_set_id = cs.id
		JOIN decks_clustersnapshot c ON c.class_cluster_id = ccs.id
		WHERE cs.live_in_production = True
		AND cs.game_format = %s
		AND ccs.player_class = %s
		AND c.external_id != -1;
	"""

	def get_signature_weights(self, game_format, player_class):
		with connection.cursor() as cursor:
			cursor.execute(
				self.LIVE_SIGNATURES_QUERY % (int(game_format), int(player_class))
			)
			result = {}
			for record in dictfetchall(cursor):
				if len(record["ccp_signature"]):
					external_id = record["external_id"]
					if external_id not in result and external_id:
						required_cards = record["required_cards"]
						result[external_id] = {
							"signature_weights": {},
							"required_cards": required_cards or [],
							"rules": record["rules"] if record["rules"] else []
						}
					for dbf_id, weight in record["ccp_signature"].items():
						result[external_id]["signature_weights"][int(dbf_id)] = weight

			return result

	def get_required_cards_for_player_class(self, game_format, player_class) -> Set[int]:
		weights = self.get_signature_weights(game_format, player_class)
		required_cards = set()
		for cluster in weights.values():
			required_cards.add(*cluster["required_cards"])
		return required_cards

	def get_live_cluster_for_archetype(self, game_format, archetype):
		return ClusterSnapshot.objects.filter(
			class_cluster__player_class=archetype.player_class,
			class_cluster__cluster_set__live_in_production=True,
			class_cluster__cluster_set__game_format=game_format,
			external_id=archetype.id
		).first()

	def get_live_signature_for_archetype(self, game_format, archetype, ccp_version=False):
		cluster = self.get_live_cluster_for_archetype(game_format, archetype)
		if not cluster:
			return None

		if ccp_version:
			return cluster.ccp_signature
		else:
			return cluster.signature


class ClusterSnapshot(models.Model, Cluster):
	id = models.AutoField(primary_key=True)
	objects = ClusterManager()
	class_cluster = models.ForeignKey(ClassClusterSnapshot, on_delete=models.CASCADE)
	cluster_id = models.IntegerField()
	experimental = models.BooleanField(default=False)
	signature = JSONField(default=dict)

	# JSON list of dbf_ids of cards required in decks before they can be classified into
	# this cluster's archetype.

	required_cards = JSONField(default=list)

	name = models.CharField(max_length=250, blank=True)
	rules = ArrayField(
		base_field=models.CharField(max_length=100, blank=True),
		default=list
	)
	data_points = JSONField(default=list)
	external_id = models.IntegerField(null=True, blank=True)
	ccp_signature = JSONField(default=dict)

	def __str__(self):
		return Cluster.__str__(self)

	@property
	def archetype(self):
		if self.external_id:
			return Archetype.objects.get(id=self.external_id)
		else:
			return None

	@property
	@mark_safe
	def pretty_signature_html(self):
		db = card_db()
		components = list(sorted(self.signature.items(), key=lambda t: t[1], reverse=True))
		table = "<table><tr><th>Card</th><th>Weight</th></tr>%s</table>"
		row = "<tr><td>%s</td><td>%s</td></tr>"
		rows = [row % (db[int(dbf)].name, round(weight, 4)) for dbf, weight in components]
		return table % "".join(rows)

	def _sankey_other_cards(self, cutoff_threshold=.1):
		data = {i: {"total_observations": 0} for i in range(0, 11)}
		db = card_db()
		other_cards = set()
		all_observations = 0
		for data_point in self.data_points:
			cards = data_point["cards"]
			for dbf_id, count in cards.items():
				card = db[int(dbf_id)]
				card_cost = min(10, card.cost)
				all_observations += data_point["observations"]
				data[card_cost]["total_observations"] += data_point["observations"]

				if dbf_id not in data[card_cost]:
					data[card_cost][dbf_id] = 0

				data[card_cost][dbf_id] += data_point["observations"]

		if all_observations > 0:
			for mana_cost, card_observations in data.items():
				total_observations = card_observations["total_observations"]
				if total_observations > 0:
					for dbf_id, observations in card_observations.items():
						if dbf_id != "total_observations":
							prevalance = float(observations / total_observations)
							if prevalance < cutoff_threshold:
								other_cards.add(dbf_id)

		return other_cards

	def sankey_visualization(self):
		nodes = set()
		links = collections.defaultdict(lambda: collections.defaultdict(int))
		db = card_db()
		other_cards = self._sankey_other_cards()
		for data_point in self.data_points:
			observations = data_point["observations"]
			cards = data_point["cards"]
			for source_dbf, count in cards.items():
				source_card = db[int(source_dbf)]
				source_cost = min(10, source_card.cost)
				for target_dbf, count in cards.items():
					target_card = db[int(target_dbf)]
					target_cost = min(10, target_card.cost)

					if target_cost == source_cost + 1:
						if source_dbf not in other_cards:
							source = str(source_dbf)
						else:
							source = "-1:%i" % source_cost

						if target_dbf not in other_cards:
							target = str(target_dbf)
						else:
							target = "-1:%i" % target_cost

						nodes.add(source)
						nodes.add(target)
						links[source][target] += observations

		result = {
			"links": [],
			"nodes": [{"name": n} for n in nodes],
		}
		for source_dbf, targets in links.items():
			for target_dbf, observations in targets.items():
				result["links"].append({
					"source": source_dbf,
					"target": target_dbf,
					"value": observations
				})

		return result


class ClusterSetSnapshot(models.Model, ClusterSet):
	id = models.AutoField(primary_key=True)
	objects = ClusterSetManager()
	as_of = models.DateTimeField(default=timezone.now)
	game_format = IntEnumField(enum=enums.FormatType, default=enums.FormatType.FT_STANDARD)
	live_in_production = models.BooleanField(default=False)
	promoted_on = models.DateTimeField(null=True)
	latest = models.BooleanField(default=False)
	training_run_id = models.IntegerField(null=True, blank=True)

	CLASS_CLUSTER_FACTORY = ClassClusterSnapshot
	CLUSTER_FACTORY = ClusterSnapshot

	class Meta:
		get_latest_by = "as_of"

	def __str__(self):
		return ClusterSet.__str__(self)

	@property
	def class_clusters(self):
		if not hasattr(self, "_class_clusters"):
			self._class_clusters = list(self.classclustersnapshot_set.all())
		return self._class_clusters

	@class_clusters.setter
	def class_clusters(self, cc):
		self._class_clusters = cc
		for class_cluster in cc:
			class_cluster.cluster_set = self

	@property
	def cluster_set_key_prefix(self):
		template = "models/{game_format}/{cluster_set_id}/{run_id}/"
		return template.format(
			game_format=self.game_format.name,
			cluster_set_id=self.id,
			run_id=self.training_run_id,
		)

	def update_all_signatures(self):
		for class_cluster in self.class_clusters:
			class_cluster.update_cluster_signatures()
			for cluster in class_cluster.clusters:
				cluster.save()

	def update_archetype_signatures(self, force=False):
		if force or all(c.neural_network_ready() for c in self.class_clusters):
			with transaction.atomic():
				ClusterSetSnapshot.objects.filter(
					live_in_production=True
				).update(live_in_production=False)
				self.live_in_production = True
				self.promoted_on = now()
				self.save()
				self.synchronize_required_cards()
				self.synchronize_deck_archetype_assignments()
		else:
			msg = "Cannot promote to live=True because the neural network is not ready"
			raise RuntimeError(msg)

	def synchronize_required_cards(self):
		for class_cluster in self.class_clusters:
			for cluster in class_cluster.clusters:
				if cluster.external_id and cluster.external_id != -1:
					archetype = Archetype.objects.get(pk=cluster.external_id)
					# Overwrite the archetype's existing required cards with the cluster's
					# required card set.
					archetype.required_cards.clear()
					for dbf_id in cluster.required_cards:
						archetype.required_cards.add(Card.objects.get(dbf_id=dbf_id))

	def synchronize_deck_archetype_assignments(self):
		for_update = collections.defaultdict(list)

		for class_cluster in self.class_clusters:
			for cluster in class_cluster.clusters:
				if cluster.external_id and cluster.external_id != -1:
					for data_point in cluster.data_points:
						digest = Deck.objects.get_digest_from_shortid(data_point["shortid"])
						for_update[cluster.external_id].append(digest)

		for external_id, digests in for_update.items():
			deck_ids = Deck.objects.filter(digest__in=digests).values_list("id", flat=True)
			Deck.objects.bulk_update_to_archetype(deck_ids, external_id)

	def train_neural_network(
		self,
		num_examples=1000000,
		max_dropped_cards=15,
		stratified=False,
		min_cards_for_determination=5,
		batch_size=1000,
		num_epochs=20,
		base_layer_size=64,
		hidden_layer_size=64,
		num_hidden_layers=2,
		working_dir=None,
		upload_to_s3=False,
		included_classes=None
	):
		start_ts = time.time()
		run_id = int(start_ts)
		self.training_run_id = run_id
		self.save()

		if working_dir:
			training_dir = working_dir
		else:
			training_dir = os.path.join(settings.BUILD_DIR, "models", str(run_id))

		if not os.path.exists(training_dir):
			os.mkdir(training_dir)

		summary_path = os.path.join(training_dir, "summary.txt")
		with open(summary_path, "w") as summary:
			summary.write("Game Format: %s\n" % self.game_format.name)
			summary.write("Cluster Set As Of: %s\n" % self.as_of.isoformat())
			summary.write("Training Run: %i\n\n" % run_id)

			summary.write("Num Examples: %i\n" % num_examples)
			summary.write("Max Dropped Cards: %i\n" % max_dropped_cards)
			summary.write("Stratified: %s\n" % str(stratified))
			summary.write("Min Cards For Determination: %i\n" % min_cards_for_determination)
			summary.write("Batch Size: %i\n" % batch_size)
			summary.write("Num Epochs: %i\n" % num_epochs)
			summary.write("Base Layer Size: %i\n" % base_layer_size)
			summary.write("Hidden Layer Size: %i\n" % hidden_layer_size)
			summary.write("Num Hidden Layers: %i\n\n" % num_hidden_layers)

			for class_cluster in self.class_clusters:
				player_class_name = class_cluster.player_class.name

				if included_classes and player_class_name not in included_classes:
					continue

				print("\nInitiating training for %s" % class_cluster.player_class.name)
				training_start = time.time()
				accuracy = class_cluster.train_neural_network(
					num_examples=num_examples,
					max_dropped_cards=max_dropped_cards,
					stratified=stratified,
					min_cards_for_determination=min_cards_for_determination,
					batch_size=batch_size,
					num_epochs=num_epochs,
					base_layer_size=base_layer_size,
					hidden_layer_size=hidden_layer_size,
					num_hidden_layers=num_hidden_layers,
					working_dir=training_dir,
					upload_to_s3=upload_to_s3
				)
				training_stop = time.time()
				duration = int(training_stop - training_start)
				print("Duration: %s seconds" % duration)
				print("Accuracy: %s" % round(accuracy, 4))

				summary.write("%s Duration: %i seconds\n" % (player_class_name, duration))
				summary.write("%s Accuracy: %s\n\n" % (player_class_name, round(accuracy, 4)))

			end_ts = time.time()
			full_duration = end_ts - start_ts
			duration_mins = int(full_duration / 60)
			duration_secs = int(full_duration % 60)
			summary.write("Full Duration: %i min(s) %i seconds\n" % (duration_mins, duration_secs))

		if upload_to_s3 and os.path.exists(summary_path):
			with open(summary_path, "rb") as summary:
				S3.put_object(
					Bucket=settings.KERAS_MODELS_BUCKET,
					Key=self.cluster_set_key_prefix + "summary.txt",
					Body=summary
				)


class ArchetypeSuggestion(models.Model):
	id = models.BigAutoField(primary_key=True)
	suggested_name = models.ForeignKey(
		"decks.ArchetypeName",
		on_delete=models.CASCADE
	)
	deck = models.ForeignKey(
		"decks.Deck",
		on_delete=models.CASCADE
	)
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		# Cascading, because otherwise we get problems with unique_together and user = NULL
		on_delete=models.CASCADE,
	)
	as_of = models.DateTimeField(default=timezone.now)

	class Meta:
		unique_together = ("deck", "user")

	def __str__(self):
		return self.suggested_name.name


class ArchetypeNameManager(models.Manager):
	def get_suggestions_for_deck(self, deck):
		player_class = deck.deck_class
		suggestions = ArchetypeName.objects.all().filter(player_class=player_class)
		return suggestions


class ArchetypeName(models.Model):
	id = models.BigAutoField(primary_key=True)
	objects = ArchetypeNameManager()
	name = models.CharField(
		max_length=250,
		blank=False
	)
	contributing_user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		blank=True,
		null=True
	)
	created = models.DateTimeField(default=timezone.now)
	player_class = IntEnumField(enum=enums.CardClass, default=enums.CardClass.INVALID)

	class Meta:
		# If only name had unique=True, users could submit wrong feedback (e.g. Zoolock on a
		# Warrior Deck) and the "Zoolock" name would be marked as WARRIOR for ever.
		unique_together = ("name", "player_class")

	def __str__(self):
		return self.name
