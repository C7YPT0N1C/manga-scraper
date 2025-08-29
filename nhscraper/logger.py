# core/logger.py
import logging
import os
from datetime import datetime

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Master log
MASTER_LOG_FILE = os.path.join(LOG_DIR, "master.log")
# Runtime log with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"runtime-{timestamp}.log")

# Logger setup
logger = logging.getLogger("nhscraper")
logger.setLevel(logging.INFO)

# LOGGING LEVELS:
# logging.debug("This is a debug message")    # Not shown because level is INFO
# logging.info("This is info")               # Shown
# logging.warning("This is a warning")       # Shown
# logging.error("This is an error")          # Shown
# logging.critical("This is critical")       # Shown

formatter = logging.Formatter("[%(levelname)s] %(message)s")

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# File handlers
fh_master = logging.FileHandler(MASTER_LOG_FILE)
fh_master.setLevel(logging.DEBUG)
fh_master.setFormatter(formatter)
logger.addHandler(fh_master)

fh_runtime = logging.FileHandler(RUNTIME_LOG_FILE)
fh_runtime.setLevel(logging.DEBUG)
fh_runtime.setFormatter(formatter)
logger.addHandler(fh_runtime)