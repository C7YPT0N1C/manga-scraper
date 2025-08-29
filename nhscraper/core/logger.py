# core/logger.py
import logging
import os
from datetime import datetime
from nhscraper.core.config import config

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Master log
MASTER_LOG_FILE = os.path.join(LOG_DIR, "master.log")
# Runtime log with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"runtime-{timestamp}.log")

# Logger setup
logger = logging.getLogger("nhscraper")
if config["VERBOSE"]: # Set to DEBUG if verbose mode is on
    logger.setLevel(logging.DEBUG)
else: # Default to INFO
    logger.setLevel(logging.INFO)

# LOGGING LEVELS - IN OTHER MODULES USE:
# logger.debug("\nThis is a debug message")    # Not shown because level is INFO
# logger.info("This is info")               # Shown
# logger.warning("\nThis is a warning")       # Shown
# logger.error("\nThis is an error")          # Shown
# logger.critical("\nThis is critical")       # Shown

formatter = logging.Formatter("[%(levelname)s] %(message)s")

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# File handlers
fh_master = logging.FileHandler(MASTER_LOG_FILE)
fh_master.setLevel(logging.DEBUG) # Master log captures all levels
fh_master.setFormatter(formatter)
logger.addHandler(fh_master)

fh_runtime = logging.FileHandler(RUNTIME_LOG_FILE)
fh_runtime.setLevel(logging.DEBUG) # Runtime log captures all levels
fh_runtime.setFormatter(formatter)
logger.addHandler(fh_runtime)