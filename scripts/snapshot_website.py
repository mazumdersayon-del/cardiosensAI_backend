"""Copy the cardiosens.ai home page into ./website so the API server can serve it at "/".

    python scripts/snapshot_website.py                 # from https://www.cardiosens.ai
    python scripts/snapshot_website.py --source URL    # another deployment of the site

The page is the prerendered Next.js output, kept byte-for-byte, plus the CSS/JS chunks,
logos and icons it references. Re-run this whenever the website changes, review the
diff and commit it. Standard library only.

What the server adds around the copy (see app/website.py and deploy.sh):
- /_next/image?url=/logos/x.png  serves the original image (no resizing).
- /blog                          redirects to the blog on www.cardiosens.ai.
- /api/contact, /api/newsletter  are forwarded by nginx to www.cardiosens.ai,
                                 where the form handlers live.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "website"
USER_AGENT = "CardiosensAI-site-snapshot/1.0"

# Same-origin assets referenced from the HTML, CSS or JS chunks.
ASSET = re.compile(
    r"""(?:["'(=\s]|^)(/(?:_next/static/[A-Za-z0-9_./-]+\.(?:js|css|woff2?|png|svg|jpe?g|webp|ico)"""
    r"""|[A-Za-z0-9_./-]+\.(?:png|svg|jpe?g|webp|gif|ico|woff2?)))"""
)
NEXT_IMAGE = re.compile(r"/_next/image\?url=([^&\"'\s]+)")
CHUNK = re.compile(r"""static/immutable/chunks/[A-Za-z0-9_.-]+\.(?:js|css)""")

ROBOTS = "User-agent: *\nDisallow: /v1/\nDisallow: /api/\nSitemap: https://cardiosens.ai/sitemap.xml\n"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - fixed site URL
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return resp.read()


def asset_paths(text: str) -> set[str]:
    paths = {m.group(1) for m in ASSET.finditer(text)}
    paths |= {urllib.parse.unquote(m.group(1)) for m in NEXT_IMAGE.finditer(text.replace("&amp;", "&"))}
    paths |= {"/_next/" + m.group(0) for m in CHUNK.finditer(text)}
    return {p for p in paths if not p.startswith("//") and ".." not in p}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", default="https://www.cardiosens.ai", help="Site to copy (default: %(default)s)")
    args = parser.parse_args()
    base = args.source.rstrip("/")

    staging = OUT.with_name("website.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()

    html = fetch(base + "/")
    (staging / "index.html").write_bytes(html)
    files = {"index.html": html}

    todo = asset_paths(html.decode("utf-8")) | {"/favicon.ico"}
    seen: set[str] = set()
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        data = fetch(base + urllib.parse.quote(path))
        rel = path.lstrip("/")
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        files[rel] = data
        if path.endswith((".js", ".css")):
            todo |= asset_paths(data.decode("utf-8", errors="replace")) - seen

    (staging / "robots.txt").write_text(ROBOTS, encoding="utf-8")
    manifest = {
        "source": base + "/",
        "copiedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())},
    }
    (staging / "SNAPSHOT.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    shutil.rmtree(OUT, ignore_errors=True)
    staging.rename(OUT)
    print(f"Copied {len(files)} files from {base} into {OUT.relative_to(ROOT)}/", file=sys.stderr)


if __name__ == "__main__":
    main()
