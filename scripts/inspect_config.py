from mangascraper.core import orchestrator
from mangascraper.core.api import api as scraperapi

print('LOG_DIR=', getattr(orchestrator,'LOG_DIR',None))
print('DB PATH=', getattr(orchestrator,'DB_PATH',None))
print('DOWNLOAD ROOTS:', scraperapi.DB.list_download_locations())
