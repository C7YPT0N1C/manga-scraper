from mangascraper.dashboard import create_app
app = create_app()
templates = ['creators.html','collections.html','scraper.html','diagnostics.html','reader.html']
for t in templates:
    try:
        app.jinja_env.get_template(t)
        print(t, 'OK')
    except Exception as e:
        print(t, 'ERROR', type(e).__name__, e)
