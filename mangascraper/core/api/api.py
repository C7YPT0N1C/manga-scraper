# mangascraper/core/api/api.py

import atexit

from mangascraper.core.api._constants import (
    db_lock,
    session_lock,
    possible_broken_symbols_lock,
    _thread_local,
    DATA_DIR,
    DB_PATH,
    CACHE_REFERENCES_TTL_SECONDS,
    CACHED_METADATA_TTL_SECONDS,
    DOWNLOAD_ROOT_MARKER_FILE,
    DOWNLOAD_ROOT_MARKER_WARNING,
    _BRACKET_PATTERN,
    _DASH_PATTERN,
    _UNDERSCORE_PATTERN,
    _SYMBOL_TRANSLATION_TABLE,
)
from mangascraper.core.api._helpers import (
    Helpers,
    prune_all_caches,
    read_cached_metadata_entry,
    clear_cached_items,
)
from mangascraper.core.api._db import DB
from mangascraper.core.api._cache import Cache
from mangascraper.core.api._get import Get
from mangascraper.core.api._fetch import Fetch
from mangascraper.core.api._build import Build
from mangascraper.core.api._sleep import Sleep
from mangascraper.core.api._progress import RuntimeProgress
from mangascraper.core.api._smart_rules import evaluate_smart_rpn, smart_expression_to_rpn

####################################################################################################################
# ATEXIT HANDLERS
####################################################################################################################

atexit.register(lambda: DB.close_connection())
atexit.register(lambda: RuntimeProgress.stop_server())

####################################################################################################################
# MODULE INIT
####################################################################################################################

Helpers.build_symbol_translation_table()

####################################################################################################################
# CONVENIENCE INSTANCES
####################################################################################################################

cache = Cache()
db = DB()
helpers = Helpers()
sleep = Sleep()
get = Get()
fetch = Fetch()