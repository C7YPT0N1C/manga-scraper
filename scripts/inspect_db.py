from mangascraper.core.api import api as scraperapi

print('Download locations:')
try:
    roots = scraperapi.DB.list_download_locations()
    for r in roots:
        print(' -', r)
except Exception as e:
    print('Failed to list download locations:', e)

print('\nSample galleries (first 10):')
try:
    rows = scraperapi.DB.list_gallery_locations() or []
    for row in rows[:10]:
        print(' -', {k: row.get(k) for k in ('gallery_id','download_path','cover_path')})
except Exception as e:
    print('Failed to fetch galleries:', e)
