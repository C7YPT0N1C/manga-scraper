#!/usr/bin/env python3
# core/logger.py

import os, logging
from datetime import datetime

from nhscraper.core.config import *

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
    log_console_level = logging.DEBUG
else:
    logger.setLevel(logging.INFO)
    log_console_level = logging.INFO

# Manual Log Level Override # TEST
logger.setLevel(logging.DEBUG)
log_console_level = logging.DEBUG

formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

# Console handler
ch = logging.StreamHandler()
ch.setLevel(log_console_level)   # Console now respects verbosity
ch.setFormatter(formatter)
logger.addHandler(ch)

# File handlers
fh_master = logging.FileHandler(MASTER_LOG_FILE)
fh_master.setLevel(logging.DEBUG)  # Always capture everything
fh_master.setFormatter(formatter)
logger.addHandler(fh_master)

fh_runtime = logging.FileHandler(RUNTIME_LOG_FILE)
fh_runtime.setLevel(logging.DEBUG)  # Always capture everything
fh_runtime.setFormatter(formatter)
logger.addHandler(fh_runtime)