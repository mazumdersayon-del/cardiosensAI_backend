"""The cardiosens.ai home page served at "/" alongside the API."""


def test_home_page_is_the_website(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "CardiosensAI" in r.text and "Know if it" in r.text
    assert r.headers["cache-control"] == "public, max-age=300"


def test_site_assets_are_served_with_long_cache(client):
    html = client.get("/").text
    css = html.split('rel="stylesheet" href="')[1].split('"')[0]
    r = client.get(css)
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_next_image_serves_the_original_file(client):
    r = client.get("/_next/image", params={"url": "/logos/cardiosensai-logo.png", "w": 1080, "q": 75})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_next_image_stays_inside_the_site(client):
    assert client.get("/_next/image", params={"url": "/../app/core/config.py"}).status_code == 404
    assert client.get("/_next/image", params={"url": "https://example.com/x.png"}).status_code == 404


def test_blog_redirects_to_public_site(client):
    r = client.get("/blog", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "https://www.cardiosens.ai/blog"
    # Next.js client navigation gets an empty answer and falls back to a full page load.
    assert client.get("/blog", headers={"RSC": "1"}, follow_redirects=False).status_code == 204


def test_api_routes_take_precedence(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/v1/me")
    assert r.status_code == 401
    r = client.get("/v1/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"
