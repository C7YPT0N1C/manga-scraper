from mangascraper.dashboard import create_app
app = create_app()
client = app.test_client()

print('GET /api/gallery/list_creators')
resp = client.get('/api/gallery/list_creators')
print(resp.status_code, resp.is_json)
try:
    print(resp.get_json() if resp.is_json else resp.data[:500])
except Exception as e:
    print('JSON error', e)

# Try list_galleries for first creator if present
if resp.is_json:
    creators = resp.get_json().get('creators', [])
    if creators:
        first = creators[0]
        name = first.get('name') or first.get('label') or ''
        print('\nGET /api/gallery/list_galleries/<creator> for', name)
        r2 = client.get(f"/api/gallery/list_galleries/{name}")
        print(r2.status_code, r2.is_json)
        try:
            print(r2.get_json() if r2.is_json else r2.data[:500])
        except Exception as e:
            print('JSON error', e)
