from mangascraper.dashboard import create_app
app = create_app()
client = app.test_client()
print('GET /api/collections/list')
rc = client.get('/api/collections/list')
print(rc.status_code, rc.is_json)
try:
    print(rc.get_json())
except Exception as e:
    print('JSON error', e)
