"""The web app (React build in ./website) served at "/" alongside the API."""

import re


def test_home_page_is_the_web_app(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in r.text and "CardiosensAI" in r.text
    assert r.headers["cache-control"] == "no-cache"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp and "'sha256-" in csp
    # Google sign-in: only its /gsi/ script, style, frame and endpoint; the page sends its origin as referrer.
    assert (
        "script-src 'self' 'sha256-" in csp
        and "https://accounts.google.com/gsi/client" in csp
        and "frame-src https://accounts.google.com/gsi/" in csp
    )
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert client.get("/healthz").headers["referrer-policy"] == "no-referrer"  # the API keeps no-referrer


def test_client_routes_fall_back_to_the_app(client):
    for path in (
        "/links",
        "/settings",
        "/reset-password?token=abc",
        "/patients/123",
        "/admin",
        "/admin/verification",
        "/admin/users?status=deleting",
    ):
        r = client.get(path)
        assert r.status_code == 200 and '<div id="root">' in r.text, path


def test_missing_files_are_404_not_the_app(client):
    assert client.get("/assets/nope.js").status_code == 404
    assert client.get("/logos/nope.png").status_code == 404


def test_built_assets_are_served_with_long_cache(client):
    html = client.get("/").text
    js = re.search(r'src="(/assets/[^"]+\.js)"', html).group(1)
    r = client.get(js)
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert client.get("/logos/cardiosensai-logo.png").headers["content-type"] == "image/png"


def test_api_routes_take_precedence(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/v1/me")
    assert r.status_code == 401
    r = client.get("/v1/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"
