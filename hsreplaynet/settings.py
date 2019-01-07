# flake8: noqa
"""
Django settings for hsreplay.net project.
"""

import os
import platform

from django.contrib.staticfiles.apps import StaticFilesConfig
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _


##
# Environments
# ENV_LIVE: True if running on *.hsreplay.net
# ENV_LAMBDA: True if running on AWS Lambda
# ENV_AWS: True if running on AWS (ENV_LIVE or ENV_LAMBDA)
# ENV_DEV: True if running in development environment

HOSTNAME = platform.node()
ENV_LIVE = HOSTNAME.endswith("hsreplay.net") or os.environ.get("HSREPLAYNET_LIVE") == "1"
ENV_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
ENV_AWS = ENV_LIVE or ENV_LAMBDA
ENV_DEV = bool(os.environ.get("HSREPLAYNET_DEBUG"))


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(BASE_DIR, "build")

SITE_ID = 1
WSGI_APPLICATION = "wsgi.application"


if ENV_DEV:
	DEBUG = True


# These apps are used on both Lambda and Web
INSTALLED_APPS_CORE = [
	"django.contrib.auth",
	"django.contrib.contenttypes",
	"django.contrib.sessions",
	"django.contrib.messages",
	"django.contrib.staticfiles",
	"django.contrib.sites",
	"raven.contrib.django.raven_compat",
	"rest_framework",
	"django_hearthstone.cards",
	"django_hearthstone.scenarios",
	"hearthsim.identity.accounts",
	"hearthsim.identity.api",
	"hsreplaynet.analytics",
	"hsreplaynet.decks",
	"hsreplaynet.features",
	"hsreplaynet.games",
	"hsreplaynet.lambdas",
	"hsreplaynet.uploads",
	"hsreplaynet.utils",
	"hsreplaynet.webhooks",
]

# The following apps are not needed on Lambda
INSTALLED_APPS_WEB = [
	"django.contrib.admin",
	"django.contrib.flatpages",
	"django.contrib.sitemaps",
	"django_reflinks",
	"allauth",
	"allauth.account",
	"allauth.socialaccount",
	"allauth.socialaccount.providers.battlenet",
	"allauth.socialaccount.providers.discord",
	"allauth.socialaccount.providers.twitch",
	"oauth2_provider",
	"django_bouncy",
	"djstripe",
	"djpaypal",
	"loginas",
	"webpack_loader",
	"hearthsim.identity.oauth2",
	"hsreplaynet.admin",
	"hsreplaynet.billing",
	"hsreplaynet.packs",
	"hsreplaynet.web",
]

INSTALLED_APPS = INSTALLED_APPS_CORE
if not ENV_LAMBDA:
	ROOT_URLCONF = "hsreplaynet.urls"
	INSTALLED_APPS += INSTALLED_APPS_WEB


##
# Databases

UPLOADS_DB = "uploads"
DATABASE_ROUTERS = ["hsreplaynet.utils.routers.UploadEventsRouter"]


MIDDLEWARE = [
	"django.contrib.sessions.middleware.SessionMiddleware",
	"django.middleware.common.CommonMiddleware",
	"django.middleware.csrf.CsrfViewMiddleware",
	"django.contrib.auth.middleware.AuthenticationMiddleware",
	"django.contrib.messages.middleware.MessageMiddleware",
	"django.middleware.clickjacking.XFrameOptionsMiddleware",
	"django.middleware.security.SecurityMiddleware",
	"django.middleware.gzip.GZipMiddleware",
	"django.middleware.locale.LocaleMiddleware",
	"django_reflinks.middleware.AnonymousReferralMiddleware",
	"django_reflinks.middleware.ReferralLinkMiddleware",
	"hsreplaynet.web.middleware.DoNotTrackMiddleware",
	"hsreplaynet.web.middleware.SetRemoteAddrFromForwardedFor",
	"hsreplaynet.web.middleware.UserLocaleMiddleware",
	"hsreplaynet.web.middleware.HostLanguageMiddleware",
	"hsreplaynet.web.middleware.MetaTagsMiddleware",
	"hsreplaynet.web.middleware.UserActivityMiddleware",
	"hsreplaynet.web.middleware.PayPalSyncMiddleware",
]


TEMPLATES = [{
	"BACKEND": "django.template.backends.django.DjangoTemplates",
	"DIRS": [
		os.path.join(BASE_DIR, "hsreplaynet", "templates")
	],
	"APP_DIRS": True,
	"OPTIONS": {
		"context_processors": [
			"django.template.context_processors.debug",
			"django.template.context_processors.request",
			"django.contrib.auth.context_processors.auth",
			"django.contrib.messages.context_processors.messages",
			"hsreplaynet.web.context_processors.ads",
			"hsreplaynet.web.context_processors.debug",
			"hsreplaynet.web.context_processors.premium",
			"hsreplaynet.web.context_processors.userdata",
		],
	},
}]


##
# Email

SERVER_EMAIL = "admin@hsreplay.net"
DEFAULT_FROM_EMAIL = "contact@hsreplay.net"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


##
# Internationalization

USE_I18N = True
USE_L10N = False
LANGUAGE_CODE = "en-us"

# Order matches the hearthstone Locale enum
LANGUAGES = (
	("en", "English"),
	# ("en-gb", "British English"),
	("fr", "Français"),
	("de", "Deutsch"),
	("ko", "한국어"),
	("es", "Español (España)"),
	("es-mx", "Español (Latinoamérica)"),
	("ru", "Русский"),
	("zh-hant", "繁體中文(台灣)"),  # zh-tw
	("zh-hans", "中文（简体）"),  # zh-cn
	("it", "Italiano"),
	("pt-br", "Português (Brasil)"),
	("pl", "Polski"),
	# ("pt", "Português"),
	("ja", "日本語"),
	("th", "ภาษาไทย"),
)
LANGUAGE_MAP = {
	"en": ("enUS", "en_US"),
	# "en-gb": ("enGB", "en_GB"),  # unused
	"fr": ("frFR", "fr_FR"),
	"de": ("deDE", "de_DE"),
	"ko": ("koKR", "ko_KR"),
	"es": ("esES", "es_ES"),
	"es-mx": ("esMX", "es_MX"),
	"ru": ("ruRU", "ru_RU"),
	"zh-hant": ("zhTW", "zh_TW"),
	"zh-hans": ("zhCN", "zh_CN"),
	"it": ("itIT", "it_IT"),
	"pl": ("plPL", "pl_PL"),
	# "pt": ("ptPT", "pt_PT"),  # unused
	"pt-br": ("ptBR", "pt_BR"),
	"ja": ("jaJP", "ja_JP"),
	"th": ("thTH", "th_TH"),
}
LANGUAGE_MAP["en-us"] = LANGUAGE_MAP["en"]
LOCALE_PATHS = [
	os.path.join(BASE_DIR, "locale", "hsreplaynet", "django"),
	os.path.join(BASE_DIR, "locale", "archetypes"),
]

LANGUAGE_COOKIE_AGE = 86400 * 365  # 1 year

USE_TZ = True
TIME_ZONE = "UTC"


##
# Static files (CSS, JavaScript, Images)

MEDIA_ROOT = os.path.join(BUILD_DIR, "media")
MEDIA_URL = "/media/"

STATIC_ROOT = os.path.join(BASE_DIR, "static")
STATIC_URL = "/static/"

STATICFILES_DIRS = [
	os.path.join(BASE_DIR, "hsreplaynet", "static"),
	os.path.join(BASE_DIR, "build", "generated"),
]
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

WEBPACK_LOADER = {
	"DEFAULT": {
		"BUNDLE_DIR_NAME": "webpack/",
		"STATS_FILE": os.path.join(BUILD_DIR, "webpack-stats.json"),
		"TIMEOUT": 5,
	}
}

if ENV_AWS:
	DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
	# STATIC_URL = "https://static.hsreplay.net/static/"
	AWS_STORAGE_BUCKET_NAME = "hsreplaynet-replays"
else:
	DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"
	AWS_STORAGE_BUCKET_NAME = None


# S3
AWS_S3_USE_SSL = True
AWS_DEFAULT_REGION = "us-east-1"
AWS_DEFAULT_ACL = "private"


AWS_IS_GZIPPED = True
GZIP_CONTENT_TYPES = [
	"text/css",
	"text/xml",
	"text/plain",
	"application/xml",
	"application/octet-stream",
]

##
# Account/Allauth settings

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
	# Needed to login by username in Django admin, regardless of `allauth`
	"django.contrib.auth.backends.ModelBackend",
	# `allauth` specific authentication methods, such as login by e-mail
	"allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_REDIRECT_URL = reverse_lazy("my_replays")
LOGIN_URL = reverse_lazy("account_login")

ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
SOCIALACCOUNT_ADAPTER = "hsreplaynet.web.battlenet.BattleNetAdapter"
SOCIALACCOUNT_PROVIDERS = {
	# NOTE: Can be removed in allauth 0.34+ (added as default scope)
	"twitch": {"SCOPE": ["user_read"]},
}


# Any subsystem wishing to use the advisory lock synchronization tools
# found in `hsreplaynet.utils.synchronization` should register the namespace
# they are using here, to prevent collisions.
ADVISORY_LOCK_NAMESPACES = {
	"REDSHIFT_ETL_MAINTENANCE_LOCK": (1, 1),
}

# If False, then we will use RQ for async processing
PROCESS_REDSHIFT_QUERIES_VIA_LAMBDA = True


##
# API
# http://www.django-rest-framework.org/api-guide/settings/

REST_FRAMEWORK = {
	# Use Django's standard `django.contrib.auth` permissions,
	# or allow read-only access for unauthenticated users.
	"DEFAULT_AUTHENTICATION_CLASSES": [
		"rest_framework.authentication.SessionAuthentication",
	],
	"DEFAULT_PERMISSION_CLASSES": [
		"rest_framework.permissions.IsAuthenticatedOrReadOnly",
	],
	"DEFAULT_PAGINATION_CLASS": "hsreplaynet.api.pagination.DefaultPagination",
	"DEFAULT_THROTTLE_CLASSES": (
		"hsreplaynet.api.throttles.PerViewUserBurstRateThrottle",
		"hsreplaynet.api.throttles.PerViewUserSustainedRateThrottle"
	),
	"DEFAULT_THROTTLE_RATES": {
		"per_view_user_burst": "10/sec",
		"per_view_user_sustained": "120/min"
	}
}


##
# OAuth2
# https://django-oauth-toolkit.readthedocs.io/en/latest/settings.html

OAUTH2_PROVIDER = {
	"SCOPES": {
		"fullaccess": _("Full access to your HSReplay.net account"),
		"account.social:read": _("Access information about your connected social accounts"),
		"collection:read": _("View your Hearthstone card collection"),
		"collection:write": _("Upload your Hearthstone card collection to the site"),
		"games:read": _("View your replays"),
		"games:write": _("Update and delete your replays"),
		"partner.stats:read": _("Access the Partner API"),
		"tournaments:read": _("Read access to the tournaments service"),
		"tournaments:write": _("Write access to the tournaments service"),
		"webhooks:read": _("View your webhooks"),
		"webhooks:write": _("Create and delete webhooks on your behalf"),
	},
	"ALLOWED_REDIRECT_URI_SCHEMES": ["https", "http"],
	"CLIENT_ID_GENERATOR_CLASS": "hsreplaynet.api.generators.ClientIdGenerator",
	"CLIENT_SECRET_GENERATOR_CLASS": "hsreplaynet.api.generators.ClientSecretGenerator",
	"SCOPES_BACKEND_CLASS": "hearthsim.identity.oauth2.models.ApplicationScopes",
	"FULL_ACCESS_SCOPE": "fullaccess",
}

OAUTH2_PROVIDER_APPLICATION_MODEL = "oauth2.Application"


if ENV_DEV:
	INSTALLED_APPS += [
		"debug_toolbar",
	]
	MIDDLEWARE += [
		"debug_toolbar.middleware.DebugToolbarMiddleware",
	]


##
# Custom site settings

HSREPLAY_CAMPAIGN = "utm_source=hsreplay.net&utm_medium=referral&utm_campaign=download"
HDT_DOWNLOAD_URL = "https://hsdecktracker.net/download/?%s" % (HSREPLAY_CAMPAIGN)
HSTRACKER_DOWNLOAD_URL = "https://hsdecktracker.net/hstracker/download/?%s" % (HSREPLAY_CAMPAIGN)
INFLUX_ENABLED = True
UPLOAD_USER_AGENT_BLACKLIST = ()
COLLECTION_UPLOAD_USER_AGENT_BLACKLIST = ()

S3_DESCRIPTORS_BUCKET = "hsreplaynet-descriptors"
S3_RAW_LOG_UPLOAD_BUCKET = "hsreplaynet-uploads"
S3_COLLECTIONS_BUCKET = "hsreplaynet-collections"

KINESIS_UPLOAD_PROCESSING_STREAM_NAME = "replay-upload-processing-stream"

# The target maximum seconds it should take for kinesis to process a backlog of raw uploads
# This value is used to periodically dynamically resize the stream capacity
KINESIS_STREAM_PROCESSING_THROUGHPUT_SLA_SECONDS = 600

LAMBDA_DEFAULT_EXECUTION_ROLE_NAME = "iam_lambda_execution_role"
LAMBDA_PRIVATE_EXECUTION_ROLE_NAME = "iam_lambda_private_vpc_execution_role"

# How much memory we give to Lambda processing instances by default
# They only need 128MB but higher memory = better CPU (= less processing time)
LAMBDA_PROCESSING_MEMORY_MB = 1024

SUCCESSFUL_UPLOAD_EVENT_REAPING_DELAY_DAYS = 5
UNSUCCESSFUL_UPLOAD_EVENT_REAPING_DELAY_DAYS = 30

JOUST_STATIC_URL = "https://joust.hearthsim.net/branches/master/"
HEARTHSTONE_ART_URL = "https://art.hearthstonejson.com/v1/256x/"
SUNWELL_URL = "https://sunwell.hearthsim.net/branches/master/"
SUNWELL_SCRIPT_URL = SUNWELL_URL + "sunwell.cdn.min.js"

HSREPLAY_DISCORD_URL = "https://discord.gg/hearthsim"
HSREPLAY_FACEBOOK_APP_ID = "1278788528798942"
HSREPLAY_FACEBOOK_URL = "https://www.facebook.com/HSReplayNet/"
HSREPLAY_TWITTER_HANDLE = "HSReplayNet"

# This setting controls whether utils.aws.clients are initialized.
# Add `CONNECT_TO_AWS = True` in local_settings.py if you need to use those locally.
CONNECT_TO_AWS = ENV_AWS

ARCHETYPE_CLASSIFICATION_ENABLED = True
ARCHETYPE_MINIMUM_SIGNATURE_MATCH_CUTOFF_DISTANCE = 5
ARCHETYPE_CORE_CARD_THRESHOLD = .8
ARCHETYPE_CORE_CARD_WEIGHT = 1
ARCHETYPE_TECH_CARD_THRESHOLD = .3
ARCHETYPE_TECH_CARD_WEIGHT = .5

ARCHETYPE_FIREHOSE_STREAM_NAME = "deck-archetype-log-stream"

REDSHIFT_LOADING_ENABLED = True
REDSHIFT_QUERY_UNLOAD_BUCKET = "hsreplaynet-analytics-results"

# This controls whether we preemptively refresh queries before they go stale from the
# refresh_stale_redshift_queries lambda
REDSHIFT_PREEMPTIVELY_REFRESH_QUERIES = True

# This controls how often we transfer records from the staging tables
# Into the production tables
REDSHIFT_ETL_TRACK_TARGET_ACTIVE_DURATION_MINUTES = 90

# This is floor(ACCOUNT_FIREHOSE_STREAM_LIMIT / NUM_STREAMS_PER_TRACK)
REDSHIFT_ETL_CONCURRENT_TRACK_LIMIT = 2

# We exclude from redshift all replays whose upload date deviates from it's
# match_start date by +/- more than this many hours
# This is intended to protect the cluster from vacuum thrash from one off
# Old replays or players with messed up system clocks
REDSHIFT_ETL_UPLOAD_DELAY_LIMIT_HOURS = 36

# Set this to True to not delete staging and pre tables during CLEANING_UP
# Make sure this is not continuously True since we're limited to 9999 tables in Redshift
REDSHIFT_ETL_KEEP_STAGING_TABLES = False

# The percent of unsorted rows that can be in a table after inserts
# Before a vacuum will be triggered. The Redshift default is 5
# However we use 0 in order to prefer small vacuums after each track loads
# Instead of intermittent large vacuums
REDSHIFT_PCT_UNSORTED_ROWS_TOLERANCE = 0

# These queues gets polled continuously and any queries in them get executed.
# The concurrency should always be less than or equal to the concurrency
# settings for the associated WLM Queue in Redshift which is used to process
# queries for that queue. Lambdas that drain the queues will wait to submit more
# queries once there are this many queries already in flight.
REDSHIFT_QUERY_QUEUES = {
	"redshift_analytics_query_queue": {
		"concurrency": 10,
		"wlm_queue": "analytics"
	},
	"redshift_personalized_query_queue": {
		"concurrency": 10,
		"wlm_queue": "personalized"
	}
}
REDSHIFT_ANALYTICS_QUERY_QUEUE_NAME = "redshift_analytics_query_queue"
REDSHIFT_PERSONALIZED_QUERY_QUEUE_NAME = "redshift_personalized_query_queue"
# Set this to True if reprocessing after a long processing pause to maintain
# an accurate distribution of games
REDSHIFT_USE_MATCH_START_AS_GAME_DATE = False

# Prescheduling Redshift Queries For Refresh Will Not Occur More Frequently Than This.
#  20 Minutes = 1200
#  1 Hour = 3600
MINIMUM_QUERY_REFRESH_INTERVAL = 1200

ARCHETYPE_QUERIES_FOR_IMMEDIATE_REFRESH = [
	"head_to_head_archetype_matchups",
	"archetype_popularity_distribution_stats",
	"list_decks_by_opponent_win_rate"
]

S3_UNLOAD_NAMESPACE = "REPLACE_ME"
S3_UNLOAD_BUCKET = "hsreplaynet-analytics-results"
KERAS_MODELS_BUCKET = "hsreplaynet-keras-models"
USE_ARCHETYPE_PREDICTION_LAMBDA = False


REDSHIFT_TRIGGER_CACHE_REFRESHES_FROM_QUERY_REQUESTS = True
REDSHIFT_TRIGGER_PERSONALIZED_DATA_REFRESHES_FROM_QUERY_REQUESTS = True
REDSHIFT_PRESCHEDULE_REFRESHES = True


WEBHOOKS = {
	"SCHEME_WHITELIST": ["http", "https"],
	"NETLOC_BLACKLIST": ["localhost"],
	"IP_BLACKLIST": ["127.0.0.1", "::1"],
	"USER_AGENT": "HSReplayNet-Webhook/1.0",
	"USE_LAMBDA": False,
}


# Analysis shows that over a 14 day period
# A sequence of 5 played cards uniquely identifies a group of
# Less than 2,000 unique possible decks, which can be safely brute force searched
DECK_PREDICTION_MINIMUM_CARDS = 3

# Set false to increase redis brute force search efficiency
INCLUDE_CURRENT_BUCKET_IN_LOOKUP = False

ILT_DECK_PREDICTION_MINIMUM_CARDS = 3
# The sliding window duration of card to deck mappings to maintain
ILT_LOOKBACK_MINS = 60 * 12
# The sliding window size of deck popularities to maintain
ILT_DECK_POPULARITY_LOOKBACK_MINS = 60 * 12

# Used in some pages such as /downloads
FONTAWESOME_CSS_URL = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css"
FONTAWESOME_CSS_INTEGRITY = "sha256-eZrrJcwDc/3uDhsdt61sL2oOBY362qM3lon1gyExkL0="


# Monkeypatch default collectstatic ignore patterns
StaticFilesConfig.ignore_patterns += ["*.scss", "*.ts", "*.tsx"]

# Whitelist analytics endpoint for analytics CORS
ANALYTICS_CORS_ORIGIN_WHITELIST = [
	"apwln3g3ia45kk690tzabfp525h9e1.ext-twitch.tv"
]
ANALYTICS_CORS_HEADER_WHITELIST = [
	"X-Twitch-Extension-Version"
]

I18N_CONTRIBUTE_URL = (
	"https://docs.google.com/document/u/1/d/e/"
	"2PACX-1vQlM_o4zvUMzrecg-RkSINT4HMxSUpiYuWeUa3VCwHMS6mIkUmRZ884VXPZ0D8E6QQLrWtBmLyNP4_d"
	"/pub?embedded=true"
)

try:
	from hsreplaynet.local_settings import *
except ImportError as e:
	# Make sure you have a `local_settings.py` file in the same directory as `settings.py`.
	# We raise a verbose error because the file is *required* in production.
	raise RuntimeError("A `local_settings.py` file could not be found or imported. (%s)" % e)


if __name__ == "__main__":
	# Invoke `python settings.py` to get a JSON dump of all settings
	import json
	import sys

	settings = {k: v for k, v in globals().items() if (
		k.isupper() and not k.startswith("_") and v.__class__.__name__ != "__proxy__"
	)}

	if len(sys.argv) > 1:
		settings = {k: v for k, v in settings.items() if k in sys.argv[1:]}

	print(json.dumps(settings, sort_keys=True, indent="\t"))
