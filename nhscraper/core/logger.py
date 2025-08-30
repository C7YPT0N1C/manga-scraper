#!/usr/bin/env python3
# core/logger.py

import os, logging
from datetime import datetime

from nhscraper.core.config import config

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Master log
#MASTER_LOG_FILE = os.path.join(LOG_DIR, "master.log")
# Runtime log with timestamp
#timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"runtime-{timestamp}.log")

# Master log # TEST
MASTER_LOG_FILE = os.path.join(LOG_DIR, "000_master.log")

# Runtime log
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"100_runtime-{timestamp}.log")


# Logger setup
logger = logging.getLogger("nhscraper")
if config["VERBOSE"]:
    logger.setLevel(logging.DEBUG)
    logger.debug("Verbose Logging Enabled.")
else: # Default to INFO
    logger.setLevel(logging.INFO)

# LOGGING LEVELS - IN OTHER MODULES USE:
# log_clarification("LOG LEVEL") # Print Blank Line (make sure logging level is the same)

# logger.debug("This is a debug message")   # Not shown because level is INFO (logger.getEffectiveLevel = 10)
# logger.info("This is info")               # Shown (logger.getEffectiveLevel = 20)
# logger.warning("This is a warning")       # Shown (logger.getEffectiveLevel = 30)
# logger.error("This is an error")          # Shown (logger.getEffectiveLevel = 40)
# logger.critical("This is critical")       # Shown (logger.getEffectiveLevel = 50)

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

def log_clarification(LEVEL):
    """Adds blank lines in terminal and log files at the requested log level."""
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    log_func_map = {
        "debug": logger.debug,
        "info": logger.info,
        "warning": logger.warning,
        "error": logger.error,
        "critical": logger.critical,
    }

    level = level_map.get(LEVEL.lower())
    log_func = log_func_map.get(LEVEL.lower())

    if level is not None and log_func is not None:
        if logger.getEffectiveLevel() <= level:  # Only log if the level is enabled
            #print("")
            log_func("")