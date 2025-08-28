#!/usr/bin/env python3
# nhscraper/cli.py

from nhscraper.config import config, logger # CLI mainly needs config values and logging
from nhscraper.downloader import main

if __name__ == "__main__":
    main()