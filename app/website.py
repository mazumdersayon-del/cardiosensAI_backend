"""The web app (patient, doctor, clinic and staff dashboards), served at "/" next to the API.

./website is the production build of ./web (React + Vite: `cd web && npm ci && npm run build`).
API routes are registered first, so /v1, /healthz and /docs always win over these files.
Client-side routes (/links, /settings, /reset-password, ...) get index.html; the app routes them.
The previous marketing page (V1.0) stays on www.cardiosens.ai; its copy is archived outside this repo.
"""

import base64
import hashlib
import re
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

WEBSITE_DIR = Path(__file__).resolve().parents[1] / "website"
API_PREFIXES = ("v1/", "v1", "docs", "redoc", "openapi.json", "healthz", "readyz")

# The page itself: no framing, no inline script other than the theme bootstrap, API on the same origin only.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'sha256-{theme}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _inline_script_hash(index_html: str) -> str:
    m = re.search(r"<script>(.*?)</script>", index_html, re.S)
    return base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode() if m else ""


class SiteFiles(StaticFiles):
    """Static files with browser caching (the API's default is no-store) and an SPA fallback."""

    def __init__(self, *args, csp: str, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.csp = csp

    async def get_response(self, path: str, scope: Scope) -> Response:
        path = path.replace("\\", "/")  # StaticFiles hands over an OS path
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.startswith(API_PREFIXES) or "." in path.rsplit("/", 1)[-1]:
                raise
            response = await super().get_response("index.html", scope)
            path = "index.html"
        if response.status_code in (200, 304):
            if path.startswith("assets/"):
                cache = "public, max-age=31536000, immutable"  # content-hashed file names
            elif path in (".", "", "index.html"):
                cache = "no-cache"  # always pick up a new release
            else:
                cache = "public, max-age=86400"
            response.headers["Cache-Control"] = cache
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, self.csp if k == "Content-Security-Policy" else v)
        return response


def install_website(app: FastAPI, directory: Path = WEBSITE_DIR) -> None:
    index = directory / "index.html"
    if not index.is_file():
        return  # API-only deployment
    csp = SECURITY_HEADERS["Content-Security-Policy"].replace("{theme}", _inline_script_hash(index.read_text()))
    app.mount("/", SiteFiles(directory=directory.resolve(), html=True, csp=csp), name="website")
