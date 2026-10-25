"""The cardiosens.ai home page, served at "/" next to the API.

The files in ./website are a copy of the public site (see scripts/snapshot_website.py).
API routes are registered first, so /v1, /healthz and /docs always win over these files.
"""

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.core.errors import not_found

WEBSITE_DIR = Path(__file__).resolve().parents[1] / "website"
PUBLIC_SITE = "https://www.cardiosens.ai"


class SiteFiles(StaticFiles):
    """Static files with browser caching (the API's default is no-store)."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            path = path.replace("\\", "/")  # StaticFiles hands over an OS path
            if path.startswith("_next/static/"):
                cache = "public, max-age=31536000, immutable"  # content-hashed file names
            elif path in (".", "index.html"):
                cache = "public, max-age=300"
            else:
                cache = "public, max-age=86400"
            response.headers["Cache-Control"] = cache
        return response


def install_website(app: FastAPI, directory: Path = WEBSITE_DIR) -> None:
    if not (directory / "index.html").is_file():
        return  # API-only deployment
    root = directory.resolve()

    @app.get("/_next/image", include_in_schema=False)
    def next_image(url: str = Query(max_length=200)) -> FileResponse:
        # Next.js's image optimiser isn't available here; serve the original file.
        path = (root / url.lstrip("/")).resolve()
        if not url.startswith("/") or root not in path.parents or not path.is_file():
            raise not_found("Image")
        return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/blog", include_in_schema=False)
    @app.get("/blog/{rest:path}", include_in_schema=False)
    def blog(request: Request, rest: str = "") -> Response:
        if request.headers.get("rsc"):
            # Next.js client-side prefetch/navigation. An empty answer makes it fall back to a
            # full page load, which then follows the redirect below (a cross-origin redirect
            # here would only fail CORS).
            return Response(status_code=204)
        return RedirectResponse(f"{PUBLIC_SITE}/blog" + (f"/{quote(rest)}" if rest else ""), status_code=308)

    app.mount("/", SiteFiles(directory=root, html=True), name="website")
