import json
import re
from collections import defaultdict
from io import StringIO

from dateutil.parser import parse as dateutil_parse
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.utils import IntegrityError
from django.utils import timezone
from django_hearthstone.cards.models import Card
from hearthstone.enums import (
	BnetGameType, BnetRegion, CardClass, CardType, FormatType, GameTag, PlayState
)
from hearthstone.utils import get_original_card_id
from hslog import LogParser, __version__ as hslog_version
from hslog.exceptions import MissingPlayerData, ParsingError
from hslog.export import EntityTreeExporter, FriendlyPlayerExporter
from hsreplay import __version__ as hsreplay_version
from hsreplay.document import HSReplayDocument
from pynamodb.exceptions import PynamoDBException

from hearthsim.identity.accounts.models import AuthToken, BlizzardAccount, Visibility
from hsredshift.etl.exceptions import CorruptReplayDataError, CorruptReplayPacketError
from hsredshift.etl.exporters import RedshiftPublishingExporter
from hsredshift.etl.firehose import flush_exporter_to_firehose
from hsreplaynet.api.live.distributions import (
	get_daily_contributor_set, get_daily_game_counter, get_live_stats_redis,
	get_played_cards_distribution, get_player_class_distribution, get_replay_feed
)
from hsreplaynet.decks.models import Deck
from hsreplaynet.games.exporters import GameDigestExporter
from hsreplaynet.uploads.models import UploadEventStatus
from hsreplaynet.uploads.utils import user_agent_product
from hsreplaynet.utils import guess_ladder_season, log
from hsreplaynet.utils.influx import influx_metric, influx_timer
from hsreplaynet.utils.instrumentation import error_handler
from hsreplaynet.utils.prediction import deck_prediction_tree, inverse_lookup_table
from hsreplaynet.vods.models import TwitchVod

from .models import (
	GameReplay, GlobalGame, GlobalGamePlayer, ReplayAlias, _generate_upload_path
)


class ProcessingError(Exception):
	pass


class GameTooShort(ProcessingError):
	pass


class UnsupportedReplay(ProcessingError):
	pass


class ReplayAlreadyExists(ProcessingError):
	def __init__(self, msg, game=None):
		self.game = game


def is_innkeeper(player):
	"""True if a player is some version of The Innkeeper (e.g. a boss) False otherwise."""

	return player.account_hi == 0 and player.account_lo == 0


def is_partial_game(meta):
	"""True if a game is reconnected or disconnected, False otherwise.

	Reconnected / disconnected games are likely to be missing some gameplay data, making
	them impossible to unify via the gameplay digest unification strategy.
	"""

	return (
		meta.get("reconnecting") or
		meta.get("disconnected")
	)


def eligible_for_unification(entity_tree, meta):
	"""True if a replay is eligible for unification, False otherwise.

	Games played via the Innkeeper aren't eligible for unification because (a) there won't
	usually be another replay submitted and (b) they're more likely than other games to
	repeat their gameplay sequences in ways that produce duplicate gameplay digests - e.g.,
	Puzzle Labs or early rounds of Dungeon Runs.
	"""

	return (
		not any(is_innkeeper(player) for player in entity_tree.players) and
		not is_partial_game(meta)
	)


def get_replay_url(shortid):
	# Not using get_absolute_url() to avoid tying into Django
	# (not necessarily avail on lambda)
	return "https://hsreplay.net/replay/%s" % (shortid)


def get_valid_match_start(match_start, upload_date):
	"""
	Returns a valid match_start value given the match_start and upload_date.
	If the upload_date is greater than the match_start, return the match_start.
	If it's greater than the match_start, return the upload_date, modified to
	use the match_start's timezone.
	"""
	if upload_date > match_start:
		return match_start

	log.info("match_start=%r>upload_date=%r - rejecting match_start", match_start, upload_date)
	return upload_date.astimezone(match_start.tzinfo)


def create_hsreplay_document(parser, entity_tree, meta, global_game):
	hsreplay_doc = HSReplayDocument.from_parser(parser, build=meta["build"])
	game_xml = hsreplay_doc.games[0]
	game_xml.id = global_game.game_handle
	game_xml.type = meta.get("hs_game_type", 0)
	game_xml.format = meta.get("format", 0)
	game_xml.scenarioID = meta.get("scenario_id", 0)
	if meta.get("reconnecting"):
		game_xml.reconnecting = True

	for player in entity_tree.players:
		player_meta = meta.get("player%i" % (player.player_id), {})
		player_xml = game_xml.players[player.player_id - 1]
		player_xml.rank = player_meta.get("rank")
		player_xml.legendRank = player_meta.get("legend_rank")
		player_xml.cardback = player_meta.get("cardback")
		player_xml.deck = player_meta.get("deck")

	return hsreplay_doc


def save_hsreplay_document(hsreplay_doc, shortid, existing_replay):
	url = get_replay_url(shortid)

	xml_str = hsreplay_doc.to_xml()
	# Add the replay's full URL as a comment
	xml_str += "\n<!-- %s -->\n" % (url)

	return ContentFile(xml_str)


def generate_globalgame_digest(packet_tree):
	digest_exporter = GameDigestExporter(packet_tree)
	digest_exporter.export()
	return digest_exporter.digest


def find_or_create_global_game(entity_tree, meta, parser):
	ladder_season = meta.get("ladder_season")
	if not ladder_season:
		ladder_season = guess_ladder_season(meta["end_time"])

	game_type = meta.get("game_type", 0)
	if game_type == 7:
		# the enum used to be wrong...
		game_type = int(BnetGameType.BGT_CASUAL_STANDARD)

	defaults = {
		"game_handle": meta.get("game_handle"),
		"server_address": meta.get("server_ip"),
		"server_port": meta.get("server_port"),
		"server_version": meta.get("server_version"),
		"game_type": game_type,
		"format": meta.get("format", 0),
		"build": meta["build"],
		"match_start": meta["start_time"],
		"match_end": meta["end_time"],
		"brawl_season": meta.get("brawl_season", 0),
		"ladder_season": ladder_season,
		"scenario_id": meta.get("scenario_id"),
		"num_entities": len(entity_tree._entities),
		"num_turns": entity_tree.tags.get(GameTag.TURN),
		"tainted_decks": False,
	}

	if eligible_for_unification(entity_tree, meta):
		# If the globalgame is eligible for unification, generate a digest
		# and get_or_create the object
		digest = generate_globalgame_digest(parser.games[0])
		log.debug("GlobalGame digest is %r" % (digest))
		global_game, created = GlobalGame.objects.get_or_create(digest=digest, defaults=defaults)
	else:
		global_game = GlobalGame.objects.create(digest=None, **defaults)
		created = True

	log.debug("Prepared GlobalGame(id=%r), created=%r", global_game.id, created)
	return global_game, created


def get_opponent_revealed_deck(entity_tree, friendly_player_id, game_type):
	for player in entity_tree.players:
		if player.player_id != friendly_player_id:
			decklist = [
				get_original_card_id(c.initial_card_id)
				for c in player.initial_deck if c.initial_card_id
			]

			deck, created = Deck.objects.get_or_create_from_id_list(
				decklist,
				hero_id=player._hero.card_id,
				game_type=game_type,
				classify_archetype=True
			)
			log.debug("Opponent revealed deck %i (created=%r)", deck.id, created)
			return deck


def find_or_create_replay(parser, entity_tree, meta, upload_event, global_game, players):
	client_handle = meta.get("client_handle") or None
	existing_replay = upload_event.game
	shortid = existing_replay.shortid if existing_replay else upload_event.shortid
	replay_xml_path = _generate_upload_path(shortid)
	log.debug("Will save replay %r to %r", shortid, replay_xml_path)

	# The user that owns the replay
	auth_token = AuthToken.objects.filter(key=upload_event.token_uuid).first()
	user = auth_token.user if auth_token else None

	friendly_player = players[meta["friendly_player"]]
	opponent_revealed_deck = get_opponent_revealed_deck(
		entity_tree,
		friendly_player.player_id,
		global_game.game_type
	)
	hsreplay_doc = create_hsreplay_document(parser, entity_tree, meta, global_game)

	common = {
		"global_game": global_game,
		"client_handle": client_handle,
		"spectator_mode": meta.get("spectator_mode", False),
		"reconnecting": meta["reconnecting"],
		"friendly_player_id": friendly_player.player_id,
	}
	defaults = {
		"shortid": shortid,
		"aurora_password": meta.get("aurora_password", ""),
		"spectator_password": meta.get("spectator_password", ""),
		"resumable": meta.get("resumable"),
		"build": meta["build"],
		"upload_token": auth_token,
		"won": friendly_player.won,
		"replay_xml": replay_xml_path,
		"hsreplay_version": hsreplay_version,
		"hslog_version": hslog_version,
		"user_agent": upload_event.user_agent,
		"opponent_revealed_deck": opponent_revealed_deck,
	}

	# Create and save hsreplay.xml file
	# Noop in the database, as it should already be set before the initial save()
	xml_file = save_hsreplay_document(hsreplay_doc, shortid, existing_replay)
	influx_metric("replay_xml_num_bytes", {"size": xml_file.size})

	if existing_replay:
		log.debug("Found existing replay %r", existing_replay.shortid)
		# Clean up existing replay file
		filename = existing_replay.replay_xml.name
		if filename and filename != replay_xml_path and default_storage.exists(filename):
			# ... but only if it's not the same path as the new one (it'll get overwritten)
			log.debug("Deleting %r", filename)
			default_storage.delete(filename)

		# Now update all the fields
		defaults.update(common)
		for k, v in defaults.items():
			setattr(existing_replay, k, v)

		# Save the replay file
		existing_replay.replay_xml.save("hsreplay.xml", xml_file, save=False)

		# Finally, save to the db and exit early with created=False
		existing_replay.save()
		return existing_replay, False

	# No existing replay, so we assign a default user/visibility to the replay
	# (eg. we never update those fields on existing replays)
	# We also prepare a webhook for triggering, if there's one.
	if user:
		defaults["user"] = user
		defaults["visibility"] = user.default_replay_visibility

	if client_handle:
		# Get or create a replay object based on our defaults
		replay, created = GameReplay.objects.get_or_create(defaults=defaults, **common)
		log.debug("Replay %r has created=%r, client_handle=%r", replay.id, created, client_handle)
	else:
		# The client_handle is the minimum we require to update an existing replay.
		# If we don't have it, we won't try deduplication, we instead get_or_create by shortid.
		defaults.update(common)
		replay, created = GameReplay.objects.get_or_create(defaults=defaults, shortid=shortid)
		log.debug("Replay %r has created=%r (no client_handle)", replay.id, created)

	if not created:
		# This can only happen if there is an inconsistency between UploadEvent.game
		# and the processing run.
		# For example, the processing crashed before UploadEvent.save(), or there are
		# multiple processing calls before UploadEvent.game is saved.
		msg = "Replay %r already exists. Try reprocessing (again)." % (shortid)
		raise ReplayAlreadyExists(msg, replay)

	# Save the replay file
	replay.replay_xml.save("hsreplay.xml", xml_file, save=False)

	if replay.shortid != upload_event.shortid:
		# We must ensure an alias for this upload_event.shortid is recorded
		# We use get or create in case this is not the first time processing this replay
		ReplayAlias.objects.get_or_create(replay=replay, shortid=upload_event.shortid)

	if user and not user.is_fake and user.webhook_endpoints.filter(is_deleted=False).exists():
		# Re-query the replay object and create an Event for it
		from hsreplaynet.webhooks.models import Event
		replay = GameReplay.objects.get(id=replay.id)
		event = Event.objects.create(
			user=user, type="replay.created", data=replay.serialize()
		)
		event.create_webhooks()

	return replay, created


def handle_upload_event_exception(e, upload_event):
	"""
	Returns a (status, reraise) tuple.
	The status will be set on the UploadEvent.
	If reraise is True, the exception will bubble up.
	"""
	if isinstance(e, ParsingError):
		return UploadEventStatus.PARSING_ERROR, False
	elif isinstance(e, (GameTooShort, EntityTreeExporter.EntityNotFound, MissingPlayerData)):
		return UploadEventStatus.UNSUPPORTED, False
	elif isinstance(e, UnsupportedReplay):
		return UploadEventStatus.UNSUPPORTED, True
	elif isinstance(e, ValidationError):
		return UploadEventStatus.VALIDATION_ERROR, False
	elif isinstance(e, ReplayAlreadyExists):
		upload_event.game = e.game
		return UploadEventStatus.SERVER_ERROR, False
	else:
		return UploadEventStatus.SERVER_ERROR, True


def process_upload_event(upload_event):
	"""
	Wrapper around do_process_upload_event() to set the event's
	status and error/traceback as needed.
	"""
	upload_event.error = ""
	upload_event.traceback = ""
	if upload_event.status != UploadEventStatus.PROCESSING:
		upload_event.status = UploadEventStatus.PROCESSING
		upload_event.save()

	try:
		replay, do_flush_exporter = do_process_upload_event(upload_event)
	except Exception as e:
		from traceback import format_exc
		upload_event.error = str(e)
		upload_event.traceback = format_exc()
		upload_event.status, reraise = handle_upload_event_exception(e, upload_event)
		metric_fields = {"count": 1}
		if upload_event.game:
			metric_fields["shortid"] = str(upload_event.game.shortid)
		influx_metric(
			"upload_event_exception",
			metric_fields,
			error=upload_event.status.name.lower()
		)
		upload_event.save()
		if reraise:
			raise
		else:
			return
	else:
		upload_event.game = replay
		upload_event.status = UploadEventStatus.SUCCESS
		upload_event.save()

	try:
		with influx_timer("redshift_exporter_flush_duration"):
			do_flush_exporter()
	except Exception as e:
		# Don't fail on this
		error_handler(e)
		influx_metric(
			"flush_redshift_exporter_error",
			{
				"count": 1,
				"error": str(e)
			}
		)

	return replay


def parse_upload_event(upload_event, meta):
	orig_match_start = dateutil_parse(meta["match_start"])
	match_start = get_valid_match_start(orig_match_start, upload_event.created)
	if match_start != orig_match_start:
		upload_event.tainted = True
		upload_event.save()
		difference = (orig_match_start - match_start).seconds
		influx_metric("tainted_replay", {"count": 1, "difference": difference})

	log_bytes = upload_event.log_bytes()
	if not log_bytes:
		raise ValidationError("The uploaded log file is empty.")

	powerlog = StringIO(log_bytes.decode("utf-8"))
	upload_event.file.close()

	parser = LogParser()
	parser._game_state_processor = "GameState"
	parser._current_date = match_start
	parser.read(powerlog)

	return parser


def fetch_active_stream_prefix():
	from hsreplaynet.uploads.models import RedshiftStagingTrack
	prefix = RedshiftStagingTrack.objects.get_active_track_prefix()
	return prefix


def validate_parser(parser, meta):
	# Validate upload
	if len(parser.games) != 1:
		raise ValidationError("Expected exactly 1 game, got %i" % (len(parser.games)))
	packet_tree = parser.games[0]
	with influx_timer("replay_exporter_duration"):
		try:
			exporter = RedshiftPublishingExporter(
				packet_tree,
				stream_prefix=fetch_active_stream_prefix()
			).export()
		except CorruptReplayPacketError as e:
			influx_metric(
				"redshift_exporter_corrupt_data_error", {
					"count": 1,
					"id": e.id,
				},
				corrupt_packet=True,
				packet_class=str(e.packet_class)
			)
			raise ValidationError(str(e))
		except (CorruptReplayDataError, MissingPlayerData) as e:
			influx_metric(
				"redshift_exporter_corrupt_data_error", {
					"count": 1,
					"exception": e.__class__.__name__,
				},
			)
			raise ValidationError(str(e))

	influx_metric("replay_game_duration", {
		"value": (packet_tree.end_time - packet_tree.start_time).total_seconds(),
	})

	game = exporter.game

	if len(game.players) != 2:
		raise ValidationError("Expected 2 players, found %i" % (len(game.players)))

	for player in game.players:
		# Set the player's name
		player.name = parser.games[0].manager.get_player_by_id(player.id).name
		if player.name is None:
			# If it's None, this is an unsupported replay.
			log.error("Cannot find player %i name. Replay not supported.", player.player_id)
			raise GameTooShort("The game was too short to parse correctly")

		player._hero = player.starting_hero
		if not player._hero:
			raise UnsupportedReplay("No hero found for player %r" % (player.name))

		try:
			db_hero = Card.objects.get(card_id=player._hero.card_id)
		except Card.DoesNotExist:
			raise UnsupportedReplay("Hero %r not found." % (player._hero))
		if db_hero.type != CardType.HERO:
			raise ValidationError("%r is not a valid hero." % (player._hero))

	friendly_player_id = packet_tree.export(cls=FriendlyPlayerExporter)
	if friendly_player_id:
		meta["friendly_player"] = friendly_player_id
	elif "friendly_player" not in meta:
		raise ValidationError("Friendly player ID not present at upload and could not guess it.")

	# We ignore "reconnecting" from the API, we only trust the log.
	# if "reconnecting" not in meta:
	# 	meta["reconnecting"] = False
	# There are two ways of identifying a reconnected game:
	# In reconnected games, the initial CREATE_GAME packet contains a STEP and STATE value.
	# In older versions of HS (pre-13xxx), STATE is RUNNING even in the CREATE_GAME packet.
	# Thankfully, looking at STEP is consistent across all versions, so we use that.
	# It will be Step.INVALID if it's NOT a reconnected game.
	meta["reconnecting"] = not not game.initial_step

	# Add the start/end time to meta dict
	meta["start_time"] = packet_tree.start_time
	meta["end_time"] = packet_tree.end_time

	return game, exporter


def _is_decklist_superset(superset_decklist, subset_decklist):
	s1 = set(superset_decklist) if superset_decklist else set()
	s2 = set(subset_decklist) if subset_decklist else set()
	return s1.issuperset(s2)


def _can_claim(blizzard_account):
	if blizzard_account.user:
		return False

	if blizzard_account.account_lo in (0, 1):
		# Blacklist known AI IDs
		return False

	return True


def _pick_decklist(meta, decklist_from_meta, replay_decklist, is_friendly_player=True):
	is_spectated_replay = meta.get("spectator_mode", False)
	is_dungeon_run = meta.get("scenario_id", 0) == 2663

	meta_decklist_is_superset = _is_decklist_superset(decklist_from_meta, replay_decklist)

	# We disregard the meta decklist if it's not matching the replay decklist
	# We always want to use it in dungeon run though, since the initial deck is garbage
	disregard_meta = not meta_decklist_is_superset and (
		not is_dungeon_run or not is_friendly_player
	)

	if not decklist_from_meta or is_spectated_replay or disregard_meta:
		# Spectated replays never know more than is in the replay data
		# But may have erroneous data from the spectator's client's memory
		# Read from before they entered the spectated game
		return replay_decklist
	else:
		return decklist_from_meta


def update_global_players(global_game, entity_tree, meta, upload_event, exporter):
	# Fill the player metadata and objects
	players = {}
	played_cards = exporter.export_played_cards()

	is_spectated_replay = meta.get("spectator_mode", False)

	for player in entity_tree.players:
		is_friendly_player = player.player_id == meta["friendly_player"]
		player_meta = meta.get("player%i" % (player.player_id), {})

		decklist_from_meta = player_meta.get("deck")
		replay_decklist = [
			get_original_card_id(c.initial_card_id)
			for c in player.initial_deck if c.initial_card_id
		]
		decklist = _pick_decklist(
			meta, decklist_from_meta, replay_decklist, is_friendly_player=is_friendly_player
		)

		player_hero_id = player._hero.card_id

		try:
			deck, _ = Deck.objects.get_or_create_from_id_list(
				decklist,
				hero_id=player_hero_id,
				game_type=global_game.game_type,
				classify_archetype=True
			)
			log.debug("Prepared deck %i (created=%r)", deck.id, _)
		except IntegrityError as e:
			# This will happen if cards in the deck are not in the DB
			# For example, during a patch release
			influx_metric("replay_deck_create_failure", {
				"count": 1,
				"build": meta["build"],
				"global_game_id": global_game.id,
				"server_ip": meta.get("server_ip", ""),
				"upload_ip": upload_event.upload_ip,
				"error": str(e),
			})
			log.exception("Could not create deck for player %r", player)
			global_game.tainted_decks = True
			# Replace with an empty deck
			deck, _ = Deck.objects.get_or_create_from_id_list([])

		capture_played_card_stats(
			global_game,
			[c.dbf_id for c in played_cards[player.player_id]],
			is_friendly_player
		)

		player_class = Deck.objects._convert_hero_id_to_player_class(player_hero_id)

		# tree-based deck prediction
		deck_prediction_enabled = getattr(settings, "FULL_DECK_PREDICTION_ENABLED", True)
		is_eligible_format = global_game.format in [FormatType.FT_STANDARD, FormatType.FT_WILD]
		is_eligible_gametype = global_game.game_type in [
			# BnetGameType.BGT_FRIENDS,
			BnetGameType.BGT_RANKED_STANDARD,
			# BnetGameType.BGT_CASUAL_STANDARD_NORMAL,
			BnetGameType.BGT_RANKED_WILD,
			# BnetGameType.BGT_CASUAL_WILD,
		]

		if deck_prediction_enabled and is_eligible_format and is_eligible_gametype:
			try:
				perform_deck_prediction(
					global_game=global_game,
					player=player,
					player_class=player_class,
					played_cards_for_player=played_cards[player.player_id],
					is_friendly_player=is_friendly_player,
					deck=deck,
				)
			except Exception as e:
				error_handler(e)

		name, _ = player.names
		if not name:
			pass

		# Create the BlizzardAccount first
		defaults = {
			"region": BnetRegion.from_account_hi(player.account_hi),
			"battletag": name,
		}

		if not is_spectated_replay and not player.is_ai and is_friendly_player:
			if upload_event.token_uuid:
				auth_token = AuthToken.objects.get(key=upload_event.token_uuid)
				user = auth_token.user
				if user and not user.is_fake:
					# and user.battletag and user.battletag.startswith(player.name):
					defaults["user"] = user

		blizzard_account, created = BlizzardAccount.objects.get_or_create(
			account_hi=player.account_hi,
			account_lo=player.account_lo,
			defaults=defaults
		)
		if not created:
			if not name:
				# Maybe we have an UNKNOWN HUMAN PLAYER for example
				# Use the BlizzardAccount's name in that case
				name = blizzard_account.battletag
			if _can_claim(blizzard_account) and "user" in defaults:
				# Set BlizzardAccount.user if it's an available claim for the user
				influx_metric("pegasus_account_claimed", {
					"count": 1,
					"account": str(blizzard_account.id),
					"region": str(blizzard_account.region),
					"account_lo": str(blizzard_account.account_lo),
					"game": str(global_game.id)
				})
				blizzard_account.user = defaults["user"]
				blizzard_account.save()
			elif "#" in name and name != blizzard_account.battletag and not player.is_ai:
				blizzard_account.battletag = name
				blizzard_account.save()

		log.debug("Prepared BlizzardAccount %r", blizzard_account)

		# Now create the GlobalGamePlayer object
		common = {
			"game": global_game,
			"player_id": player.player_id,
		}
		defaults = {
			"is_first": player.tags.get(GameTag.FIRST_PLAYER, False),
			"is_ai": player.is_ai,
			"hero_id": player_hero_id,
			"hero_premium": player._hero.tags.get(GameTag.PREMIUM, False),
			"final_state": player.tags.get(GameTag.PLAYSTATE, 0),
			"extra_turns": player.tags.get(GameTag.EXTRA_TURNS_TAKEN_THIS_GAME, 0),
			"deck_list": deck,
		}

		update = {
			"name": name,
			"pegasus_account": blizzard_account,
			"rank": player_meta.get("rank"),
			"legend_rank": player_meta.get("legend_rank"),
			"stars": player_meta.get("stars"),
			"wins": player_meta.get("wins"),
			"losses": player_meta.get("losses"),
			"deck_id": player_meta.get("deck_id") or None,
			"cardback_id": player_meta.get("cardback"),
		}

		defaults.update(update)
		game_player, created = GlobalGamePlayer.objects.get_or_create(defaults=defaults, **common)
		log.debug("Prepared player %r (%i) (created=%r)", game_player, game_player.id, created)

		if not created:
			# Go through the update dict and update values on the player
			# This gets us extra data we might not have had when the player was first created
			updated = False
			for k, v in update.items():
				if v and getattr(game_player, k) != v:
					setattr(game_player, k, v)
					updated = True

			# Skip updating the deck if we already have a bigger one
			# TODO: We should make deck_list nullable and only create it here
			if game_player.deck_list.size is None or len(decklist) > game_player.deck_list.size:
				# XXX: Maybe we should also check friendly_player_id for good measure
				game_player.deck_list = deck
				updated = True

			if updated:
				log.debug("Saving updated player to the database.")
				game_player.save()

		players[player.player_id] = game_player

	return players


def perform_deck_prediction(
	global_game, player, player_class, played_cards_for_player, is_friendly_player, deck
):
	tree = deck_prediction_tree(player_class, global_game.format)

	# 5 played cards partitions a 14 day window into buckets of ~ 500 or less
	# We can search through ~ 2,000 decks in 100ms so that gives us plenty of headroom
	min_played_cards = tree.max_depth - 1

	# We can control via settings the minumum number of cards we need
	# To know about in the deck list before we attempt to guess the full deck
	min_observed_cards = settings.DECK_PREDICTION_MINIMUM_CARDS

	# sorted_played_cards = sorted(played_cards_for_player, key=lambda c: c.cost)
	played_card_dbfs = [c.dbf_id for c in played_cards_for_player][:min_played_cards]
	played_card_names = [c.name for c in played_cards_for_player][:min_played_cards]

	if deck.size is not None:
		deck_size = deck.size
	else:
		deck_size = sum(i.count for i in deck.includes.all())

	has_enough_observed_cards = deck_size >= min_observed_cards
	has_enough_played_cards = len(played_card_dbfs) >= min_played_cards

	is_eligible = has_enough_observed_cards and has_enough_played_cards
	influx_metric(
		"deck_prediction_eligibility",
		{
			"deck_id": deck.id,
			"is_eligible": 1 if is_eligible else 0
		},
		has_enough_observed_cards=has_enough_observed_cards,
		has_enough_played_cards=has_enough_played_cards,
		player_class=CardClass(int(player_class)).name,
		format=FormatType(int(global_game.format)).name,
		num_observed_cards=deck_size,
		num_played_cards=len(played_card_dbfs)
	)

	if deck_size == 30:
		# we have the full deck as a datapoint
		perform_cross_validation(
			tree=tree,
			global_game=global_game,
			player=player,
			player_class=player_class,
			is_friendly_player=is_friendly_player,
			deck=deck,
			played_cards_for_player=played_cards_for_player,
			played_card_dbfs=played_card_dbfs,
		)
		observe_deck(
			tree=tree,
			deck=deck,
			played_card_dbfs=played_card_dbfs
		)

		# deck_id == proxy_deck_id for complete decks
		deck.guessed_full_deck = deck
		deck.save()

		influx_metric(
			"deck_prediction_funnel",
			{
				"actual_deck_id": deck.id,
			},
			is_friendly_player=is_friendly_player,
			player_class=CardClass(int(player_class)).name,
			format=FormatType(int(global_game.format)).name,
			is_complete_deck=True,
			deck_size=deck_size,
			is_eligible=is_eligible,
			is_prediction_success=None,
			deck_has_archetype=deck.archetype_id is not None,
			num_played_cards=len(played_cards_for_player)
		)

	elif is_eligible:
		# we have enough cards to predict the full deck
		guessed_full_deck = predict_deck(
			tree=tree,
			global_game=global_game,
			player_class=player_class,
			deck=deck,
			deck_size=deck_size,
			played_card_dbfs=played_card_dbfs,
			played_card_names=played_card_names,
		)

		if guessed_full_deck:
			guessed_deck_id = guessed_full_deck.id
			guessed_archetype = guessed_full_deck.archetype_id
			deck.guessed_full_deck = guessed_full_deck
			deck.save()
		else:
			guessed_deck_id = None
			guessed_archetype = None

		influx_metric(
			"deck_prediction_funnel",
			{
				"actual_deck_id": deck.id,
				"predicted_deck_id": guessed_deck_id
			},
			is_friendly_player=is_friendly_player,
			player_class=CardClass(int(player_class)).name,
			format=FormatType(int(global_game.format)).name,
			is_complete_deck=False,
			deck_size=deck_size,
			is_eligible=True,
			is_prediction_success=(guessed_full_deck is not None),
			deck_has_archetype=deck.archetype_id is not None,
			prediction_has_archetype=(guessed_archetype is not None),
			num_played_cards=len(played_cards_for_player)
		)
	else:
		influx_metric(
			"deck_prediction_funnel",
			{
				"actual_deck_id": deck.id,
			},
			is_friendly_player=is_friendly_player,
			player_class=CardClass(int(player_class)).name,
			format=FormatType(int(global_game.format)).name,
			is_complete_deck=False,
			deck_size=deck_size,
			is_eligible=False,
			is_prediction_success=None,
			deck_has_archetype=deck.archetype_id is not None,
			prediction_has_archetype=None,
			num_played_cards=len(played_cards_for_player)
		)


def perform_cross_validation(
	tree, global_game, player, player_class, is_friendly_player, deck, played_cards_for_player,
	played_card_dbfs
):
	cross_val_dbf_map = {}
	for c in played_cards_for_player:
		if c.dbf_id not in cross_val_dbf_map:
			cross_val_dbf_map[c.dbf_id] = 1
		else:
			cross_val_dbf_map[c.dbf_id] += 1

	cross_val_result = tree.lookup(
		cross_val_dbf_map,
		played_card_dbfs,
	)

	if cross_val_result.predicted_deck_id:
		cross_val_deck = Deck.objects.get(
			id=cross_val_result.predicted_deck_id
		)

		perfect_deck_match = deck.id == cross_val_deck.id
		archetype_match = deck.archetype_id == cross_val_deck.archetype_id
		prediction_has_archetype = cross_val_deck.archetype_id is not None
		predicted_deck_id = cross_val_deck.id
		predicted_archetype_id = cross_val_deck.archetype_id
	else:
		perfect_deck_match = False
		archetype_match = False
		prediction_has_archetype = False
		predicted_deck_id = None
		predicted_archetype_id = None

	actual_has_archetype = deck.archetype_id is not None
	final_state = player.tags.get(GameTag.PLAYSTATE, 0)

	influx_metric(
		"deck_prediction_validation",
		{
			"actual_deck_id": deck.id,
			"predicted_deck_id": predicted_deck_id
		},
		perfect_deck_match=perfect_deck_match,
		archetype_match=archetype_match,
		player_class=CardClass(int(player_class)).name,
		format=FormatType(int(global_game.format)).name,
		prediction_has_archetype=prediction_has_archetype,
		predicted_archetype_id=predicted_archetype_id,
		actual_archetype_id=deck.archetype_id,
		actual_has_archetype=actual_has_archetype,
		is_friendly_player=is_friendly_player,
		num_played_cards=len(played_cards_for_player),
		final_state=PlayState(int(final_state)).name
	)


def observe_deck(tree, deck, played_card_dbfs):
	tree.observe(
		deck.id,
		deck.dbf_map(),
		played_card_dbfs
	)


def predict_deck(
	tree, global_game, player_class, deck, deck_size, played_card_dbfs, played_card_names
):
	res = tree.lookup(
		deck.dbf_map(),
		played_card_dbfs,
	)
	predicted_deck_id = res.predicted_deck_id

	fields = {
		"actual_deck_id": deck.id,
		"deck_size": deck_size,
		"game_id": global_game.id,
		"sequence": "->".join("[%s]" % c for c in played_card_names),
		"predicted_deck_id": res.predicted_deck_id,
		"match_attempts": res.match_attempts,
		"tie": res.tie
	}

	if res.node:
		fields["depth"] = res.node.depth

	tree_depth = res.node.depth if res.node else None
	influx_metric(
		"deck_prediction",
		fields,
		missing_cards=30 - deck_size,
		player_class=CardClass(int(player_class)).name,
		format=FormatType(int(global_game.format)).name,
		tree_depth=tree_depth,
		made_prediction=predicted_deck_id is not None
	)

	if not predicted_deck_id:
		return None

	return Deck.objects.get(id=predicted_deck_id)


def perform_ilt_deck_prediction(game_format, player_class, deck, upload_event):
	ilt = inverse_lookup_table(game_format, player_class)
	# for now, let's just observe complete decks
	if deck.size == 30:
		with influx_timer("ilt_deck_prediction_duration", method="observe"):
			ilt.observe(deck.dbf_map(), deck.id, uuid=upload_event.shortid)


def update_replay_feed(replay):
	try:
		if replay.global_game.exclude_from_statistics:
			return

		if replay.user is not None and replay.user.default_replay_visibility != Visibility.Public:
			return

		elapsed_minutes = elapsed_seconds_from_match_end(replay.global_game) / 60.0
		if elapsed_minutes > 5.0:
			return

		if (
			BnetGameType(replay.global_game.game_type) != BnetGameType.BGT_RANKED_STANDARD or
			FormatType(replay.global_game.format) != FormatType.FT_STANDARD
		):
			return

		player1 = replay.player(1)
		player2 = replay.player(2)

		player1_archetype = player1.deck_list.archetype
		player2_archetype = player2.deck_list.archetype

		player1_won = (replay.friendly_player_id == 1) == replay.won
		player2_won = (replay.friendly_player_id == 2) == replay.won

		if (
			not player1_archetype or
			not player2_archetype or
			not (player1.rank or player1.legend_rank) or
			not (player2.rank or player2.legend_rank)
		):
			return

		data = {
			"player1_archetype": player1_archetype.id,
			"player1_rank": player1.rank,
			"player1_legend_rank": player1.legend_rank,
			"player1_won": player1_won,
			"player2_archetype": player2_archetype.id,
			"player2_rank": player2.rank,
			"player2_legend_rank": player2.legend_rank,
			"player2_won": player2_won,
			"id": replay.shortid
		}

		def comparator(d1, d2):
			keys = [key for key in data.keys() if key != "id"]
			return all([key in d1 and key in d2 and str(d1[key]) == str(d2[key]) for key in keys])

		success = get_replay_feed(comparator).push(data)
		influx_metric("update_replay_feed", {"count": 1}, success=success)

	except Exception as e:
		error_handler(e)


def update_game_counter(replay):
	try:
		get_daily_game_counter().increment()
		friendly_account = replay.player(replay.friendly_player_id).pegasus_account
		player_id = "%s_%s" % (int(friendly_account.region), friendly_account.account_lo)
		get_daily_contributor_set().add(player_id)
	except Exception as e:
		error_handler(e)


def update_last_replay_upload(upload_event):
	"""Update the last replay upload timestamp for the uploading user if user is known."""

	product = user_agent_product(upload_event.user_agent) \
		if upload_event.user_agent else None

	if product in ("HDT", "HDTPortable", "HSTracker"):

		# The purpose of setting the last replay upload timestamp is to be able to set the
		# "HDT User" tag in MailChimp, so only bump the timestamp on certain user agents.

		auth_token = AuthToken.objects.filter(key=upload_event.token_uuid).first()
		user = auth_token.user if auth_token else None
		if user:
			user.last_replay_upload = timezone.now()
			user.save()


def update_player_class_distribution(replay):
	try:
		game_type_name = BnetGameType(replay.global_game.game_type).name
		distribution = get_player_class_distribution(game_type_name, use_lua=False)
		opponent = replay.opposing_player
		player_class = opponent.hero_class_name
		distribution.increment(player_class, win=opponent.won)
	except Exception as e:
		error_handler(e)


def elapsed_seconds_from_match_end(global_game):
	current_ts = timezone.now()
	match_end = global_game.match_end
	diff = current_ts - match_end
	return abs(diff.total_seconds())


def capture_played_card_stats(global_game, played_cards, is_friendly_player):
	try:
		elapsed_minutes = elapsed_seconds_from_match_end(global_game) / 60.0
		if not is_friendly_player and elapsed_minutes <= 5.0:
			game_type_name = BnetGameType(global_game.game_type).name
			redis = get_live_stats_redis()
			dist = get_played_cards_distribution(
				game_type_name,
				redis_client=redis,
				use_lua=False
			)
			for dbf_id in played_cards:
				dist.increment(dbf_id)
	except Exception as e:
		error_handler(e)


def update_game_meta(parser, meta):
	if parser.game_meta:
		meta["build"] = int(parser.game_meta["BuildNumber"])
		meta["scenario_id"] = int(parser.game_meta["ScenarioID"])
		meta["hs_game_type"] = int(parser.game_meta["GameType"])
		format_type = parser.game_meta["FormatType"]
		is_wild = format_type == FormatType.FT_WILD
		meta["format"] = int(format_type)
		meta["game_type"] = int(parser.game_meta["GameType"].as_bnet(wild=is_wild))


TWITCH_VOD_URL_PATTERN = re.compile(
	r"^https://www\.twitch\.tv/videos/\d+\?t=\d+h\d{1,2}m\d{1,2}s$"
)


def has_twitch_vod_url(meta):
	"""Returns true if the specified metadata contains Twitch VOD metadata, False otherwise.

	:param meta: the upload metadata dict
	:return: True if Twitch VOD metadata is present, False otherwise
	"""

	if "twitch_vod" not in meta:
		return False

	vod_meta = meta["twitch_vod"]

	if isinstance(vod_meta, dict):
		if "channel_name" not in vod_meta or "url" not in vod_meta:
			return False

		# Does the VOD URL look like it came from Twitch?

		return TWITCH_VOD_URL_PATTERN.fullmatch(vod_meta["url"]) is not None
	else:
		return False


def record_twitch_vod(replay, meta):
	"""Persist a Twitch VOD link to DynamoDB using the specified replay and upload metadata.

	This function should be called only if a previous call to "has_twitch_vod_url" above
	returns True.

	:param replay: the GameReplay to use to generate the Twitch VOD link
	:param meta: the metadata dict to use to look up Twitch metadata
	:return:
	"""

	game = replay.global_game
	game_length = game.match_end.timestamp() - game.match_start.timestamp()

	friendly_player = replay.friendly_player
	friendly_deck = friendly_player.deck_list

	vod_meta = meta["twitch_vod"]
	twitch_vod_url = vod_meta["url"]

	try:
		twitch_vod = TwitchVod(
			twitch_channel_name=vod_meta["channel_name"],
			friendly_player_name=friendly_player.name,
			hsreplaynet_user_id=replay.user.id,
			replay_shortid=replay.shortid,
			rank=friendly_player.rank or 0,
			won=bool(replay.won),
			went_first=bool(friendly_player.is_first),
			game_date=game.match_start.timestamp(),
			game_length_seconds=game_length,
			format_type=FormatType(game.format).name,
			game_type=BnetGameType(game.game_type).name,
			friendly_player_canonical_deck_string=friendly_deck.deckstring,
			url=twitch_vod_url
		)

		if friendly_deck.archetype:
			twitch_vod.friendly_player_archetype_id = friendly_deck.archetype.id

		# Generate the "combined rank" synthetic range key.

		if friendly_player.legend_rank and friendly_player.legend_rank > 0:
			twitch_vod.legend_rank = friendly_player.legend_rank
			twitch_vod.combined_rank = "L%s" % friendly_player.legend_rank
		else:
			twitch_vod.combined_rank = "R%s" % friendly_player.rank

		opposing_player = replay.opposing_player

		twitch_vod.opposing_player_class = opposing_player.hero_class_name

		if not opposing_player.is_ai:
			opposing_deck = opposing_player.deck_list
			if opposing_deck.guessed_full_deck:
				opposing_deck = opposing_deck.guessed_full_deck
			if opposing_deck.archetype:
				twitch_vod.opposing_player_archetype_id = opposing_deck.archetype.id

		with influx_timer("twitch_vod_persist_duration"):
			twitch_vod.save()

		influx_metric("twitch_vods", {"count": 1})

	except PynamoDBException as e:

		# The most likely error we'll encounter is PutErrors stemming from an AWS
		# "ProvisionedThroughputExceededException." We don't want those to block the rest
		# of replay persistence, so just log a metric for now.

		influx_metric("twitch_vod_persist_failures", {"count": 1})
		log.warning("Failed to persist Twitch VOD %s: %s", twitch_vod_url, e)

	except Exception as e:

		# Temporarily catch all Exceptions coming out of DynamoDB persistence

		influx_metric("twitch_vod_persist_exceptions", {"count": 1})
		log.error("Failed to persist Twitch VOD %s: %s", twitch_vod_url, e)


def do_process_upload_event(upload_event):
	meta = json.loads(upload_event.metadata)

	# Hack until we do something better
	# We need the correct tz, but here it's stored as UTC because it goes through DRF
	# https://github.com/encode/django-rest-framework/commit/7d6d043531
	if upload_event.descriptor_data:
		descriptor_data = json.loads(upload_event.descriptor_data)
		meta["match_start"] = descriptor_data["upload_metadata"]["match_start"]

	# Parse the UploadEvent's file
	parser = parse_upload_event(upload_event, meta)
	# Validate the resulting object and metadata
	entity_tree, exporter = validate_parser(parser, meta)

	# For build 23576 and up
	update_game_meta(parser, meta)

	# Create/Update the global game object and its players
	global_game, global_game_created = find_or_create_global_game(entity_tree, meta, parser)
	players = update_global_players(global_game, entity_tree, meta, upload_event, exporter)

	# Create/Update the replay object itself
	replay, game_replay_created = find_or_create_replay(
		parser, entity_tree, meta, upload_event, global_game, players
	)

	product = user_agent_product(upload_event.user_agent) \
		if upload_event.user_agent else None

	influx_metric(
		"game_replays_uploaded",
		{"count": 1},
		user_agent=product,
		v2_unification=not global_game_created
	)

	update_last_replay_upload(upload_event)

	update_player_class_distribution(replay)
	update_replay_feed(replay)
	update_game_counter(replay)

	# Persist Twitch VOD metadata to DynamoDB if present

	if has_twitch_vod_url(meta):
		record_twitch_vod(replay, meta)

	# Defer flushing the exporter until after the UploadEvent is set to SUCCESS
	# So that the player can start watching their replay sooner
	def do_flush_exporter():
		can_attempt_redshift_load = False

		if global_game.loaded_into_redshift is None:
			log.debug("Global game has not been loaded into redshift.")
			# Attempt to claim the advisory_lock, if successful:
			can_attempt_redshift_load = global_game.acquire_redshift_lock()
			if can_attempt_redshift_load:
				# We update GlobalGame to see whether another process might have updated
				# loaded_into_redshift in the meantime
				global_game.refresh_from_db()
				if global_game.loaded_into_redshift is not None:
					can_attempt_redshift_load = False
		else:
			log.debug("Global game has already been loaded into Redshift")

		# Only if we were able to claim the advisory lock do we proceed here.
		if can_attempt_redshift_load:
			log.debug("Redshift lock acquired. Will attempt to flush to redshift")

			if should_load_into_redshift(upload_event, meta, global_game, exporter):
				with influx_timer("generate_redshift_game_info_duration"):
					game_info = get_game_info(global_game, replay)
				exporter.set_game_info(game_info)

				try:
					with influx_timer("flush_exporter_to_firehose_duration"):
						flush_failures_report = flush_exporter_to_firehose(
							exporter,
							records_to_flush=get_records_to_flush()
						)
						for target_table, errors in flush_failures_report.items():
							for error in errors:
								influx_metric(
									"firehose_flush_failure",
									{
										"stream_name": error["stream_name"],
										"error_code": error["error_code"],
										"error_message": error["error_message"],
										"count": 1
									},
									target_table=target_table
								)
				except Exception:
					raise
				else:
					global_game.loaded_into_redshift = timezone.now()
					global_game.save()
					# Okay to release the advisory lock once loaded_into_redshift is set
					# It will also be released automatically when the lambda exits.
					global_game.release_redshift_lock()
		else:
			log.debug("Did not acquire redshift lock. Will not flush to redshift")

	return replay, do_flush_exporter


def get_records_to_flush():
	from hsredshift.etl.records import STAGING_RECORDS
	from hsreplaynet.uploads.models import RedshiftStagingTrack
	active_track = RedshiftStagingTrack.objects.get_active_track()
	staging_records = {r.REDSHIFT_TABLE: r for r in STAGING_RECORDS}
	result = []
	for table in active_track.tables.all():
		if table.target_table in staging_records:
			result.append(staging_records[table.target_table])

	return result


REDSHIFT_GAMETYPE_WHITELIST = (
	BnetGameType.BGT_ARENA,
	BnetGameType.BGT_FRIENDS,
	BnetGameType.BGT_RANKED_STANDARD,
	BnetGameType.BGT_RANKED_WILD,
	BnetGameType.BGT_TAVERNBRAWL_1P_VERSUS_AI,
	BnetGameType.BGT_TAVERNBRAWL_2P_COOP,
	BnetGameType.BGT_TAVERNBRAWL_PVP,
	BnetGameType.BGT_FSG_BRAWL_1P_VERSUS_AI,
	BnetGameType.BGT_FSG_BRAWL_2P_COOP,
	BnetGameType.BGT_FSG_BRAWL_VS_FRIEND,
	BnetGameType.BGT_VS_AI,
)


def should_load_into_redshift(upload_event, meta, global_game, exporter):
	if not settings.ENV_AWS or not settings.REDSHIFT_LOADING_ENABLED:
		return False

	if upload_event.test_data:
		return False

	# Reconnected / disconnected games aren't that valuable, plus it's likely that they'll
	# be loaded into Redshift via another replay.

	if is_partial_game(meta):
		return False

	if global_game.loaded_into_redshift:
		return False

	if global_game.exclude_from_statistics:
		return False

	if global_game.tainted_decks:
		return False

	if global_game.game_type not in REDSHIFT_GAMETYPE_WHITELIST:
		return False

	# Don't load games that are clearly corrupt (scrambled turn sequence, missing terminal
	# state).

	if not exporter.is_valid_final_state:
		return False

	# We only load games in where the match_start date is within +/ 36 hours from
	# The upload_date. This filters out really old replays people might upload
	# Or replays from users with crazy system clocks.
	# The purpose of this filtering is to do reduce variability and thrash in our vacuuming
	# If we determine that vacuuming is not a bottleneck than we can consider
	# relaxing this requirement.

	upload_date = upload_event.log_upload_date
	match_start = global_game.match_start

	# Don't load replays older than a threshold window (by default 36 hours)

	meets_req, diff_hours = _dates_within_etl_threshold(upload_date, match_start)
	if not meets_req:
		influx_metric("replay_failed_recency_requirement", {"count": 1, "diff": diff_hours})
	return meets_req


def _dates_within_etl_threshold(d1, d2):
	threshold_hours = settings.REDSHIFT_ETL_UPLOAD_DELAY_LIMIT_HOURS
	diff = d1 - d2
	diff_hours = abs(diff.total_seconds()) / 3600.0
	within_threshold = diff_hours <= threshold_hours
	return within_threshold, diff_hours


def get_game_info(global_game, replay):
	player1 = replay.player(1)
	player2 = replay.player(2)

	with influx_timer("generate_redshift_player_decklists_duration"):
		player1_decklist = player1.deck_list.as_dbf_json()
		player2_decklist = player2.deck_list.as_dbf_json()

	if settings.REDSHIFT_USE_MATCH_START_AS_GAME_DATE and global_game.match_start:
		game_date = global_game.match_start.date()
	else:
		game_date = timezone.now().date()

	game_info = {
		"game_id": int(global_game.id),
		"shortid": replay.shortid,
		"game_type": int(global_game.game_type),
		"scenario_id": global_game.scenario_id,
		"ladder_season": global_game.ladder_season,
		"brawl_season": global_game.brawl_season,
		"match_start": global_game.match_start,
		"game_date": game_date,
		"players": {
			"1": {
				"deck_id": int(player1.deck_list.id),
				"archetype_id": get_archetype_id(player1),
				"deck_list": player1_decklist,
				"rank": 0 if player1.legend_rank else player1.rank if player1.rank else -1,
				"legend_rank": player1.legend_rank,
				"full_deck_known": player1.deck_list.is_full_deck
			},
			"2": {
				"deck_id": int(player2.deck_list.id),
				"archetype_id": get_archetype_id(player2),
				"deck_list": player2_decklist,
				"rank": 0 if player2.legend_rank else player2.rank if player2.rank else -1,
				"legend_rank": player2.legend_rank,
				"full_deck_known": player2.deck_list.is_full_deck
			},
		}
	}

	if player1.deck_list.guessed_full_deck:
		player1_proxy_deck = player1.deck_list.guessed_full_deck
		player1_proxy_decklist = player1_proxy_deck.as_dbf_json()
		game_info["players"]["1"]["proxy_deck_id"] = player1_proxy_deck.id
		game_info["players"]["1"]["proxy_deck_list"] = player1_proxy_decklist

	if player2.deck_list.guessed_full_deck:
		player2_proxy_deck = player2.deck_list.guessed_full_deck
		player2_proxy_decklist = player2_proxy_deck.as_dbf_json()
		game_info["players"]["2"]["proxy_deck_id"] = player2_proxy_deck.id
		game_info["players"]["2"]["proxy_deck_list"] = player2_proxy_decklist

	return game_info


def get_archetype_id(p):
	return int(p.deck_list.archetype.id) if p.deck_list.archetype else None


def _get_tuple_decklist(cards, card_id_db):
	card_dict = defaultdict(int)
	for card_id in cards:
		dbf_id = card_id_db[card_id].dbf_id
		card_dict[dbf_id] += 1
	return sorted(card_dict.items())
