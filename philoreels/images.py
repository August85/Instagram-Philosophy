"""Download public-domain background pictures for the reels.

Two free sources are searched, and only images that are explicitly public
domain or CC0 are kept, so no credit or licence fee is ever owed:

- The Metropolitan Museum of Art Open Access collection (CC0), which has many
  Indian paintings of Krishna and scenes from the Mahabharata.
- Wikimedia Commons, filtered to "Public domain" / CC0 files only.

Pictures are saved to assets/backgrounds/ on this machine with a CREDITS.md
listing where each one came from. Delete any you don't like; reels pick from
whatever is left.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from PIL import Image

USER_AGENT = "philoreels/0.1 (Instagram philosophy reels; https://github.com/August85/Instagram-Philosophy)"

MET_QUERIES = ["Krishna", "Krishna and Arjuna", "Bhagavad Gita", "Arjuna", "Vishnu", "Radha Krishna", "Mahabharata"]
COMMONS_QUERIES = [
    "Krishna Arjuna chariot painting",
    "Bhagavad Gita painting",
    "Krishna Arjuna Kurukshetra",
    "Vishvarupa Krishna painting",
    "Raja Ravi Varma Krishna",
    "Krishna painting miniature",
    "Krishna flute painting",
    "Ganges sunrise Varanasi",
    "Himalaya sunrise",
    "lotus flower",
]

PD_LICENSES = re.compile(r"^(public domain|pd\b|pd-|cc0|cc-zero|no restrictions)", re.I)
MIN_SIDE = 900


@dataclass
class Picture:
    source: str
    key: str
    title: str
    artist: str
    license: str
    page_url: str
    image_url: str

    @property
    def filename(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")[:50] or "image"
        h = hashlib.sha1(self.key.encode()).hexdigest()[:8]
        return f"{self.source}-{slug}-{h}.jpg"


def is_public_domain(license_name: str) -> bool:
    return bool(license_name and PD_LICENSES.search(license_name.strip()))


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


class Fetcher:
    def __init__(self, session: requests.Session | None = None):
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", USER_AGENT)

    # -- The Met ---------------------------------------------------------------
    def met(self, queries: Iterable[str] = MET_QUERIES, per_query: int = 40) -> list[Picture]:
        base = "https://collectionapi.metmuseum.org/public/collection/v1"
        seen: set[int] = set()
        out: list[Picture] = []
        for q in queries:
            r = self.http.get(f"{base}/search", params={"q": q, "hasImages": "true"}, timeout=60)
            ids = (r.json().get("objectIDs") or [])[:per_query]
            for oid in ids:
                if oid in seen:
                    continue
                seen.add(oid)
                obj = self.http.get(f"{base}/objects/{oid}", timeout=60).json()
                if not obj.get("isPublicDomain") or not obj.get("primaryImage"):
                    continue
                if obj.get("classification") and not re.search(r"paint|drawing|manuscript|album|codic", obj["classification"], re.I):
                    continue
                out.append(Picture(
                    source="met", key=str(oid), title=obj.get("title") or f"Met {oid}",
                    artist=obj.get("artistDisplayName") or obj.get("culture") or "Unknown",
                    license="CC0 (Met Open Access)", page_url=obj.get("objectURL", ""),
                    image_url=obj["primaryImage"],
                ))
        return out

    # -- Wikimedia Commons -------------------------------------------------------
    def commons(self, queries: Iterable[str] = COMMONS_QUERIES, per_query: int = 30) -> list[Picture]:
        api = "https://commons.wikimedia.org/w/api.php"
        out: list[Picture] = []
        seen: set[str] = set()
        for q in queries:
            params = {
                "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}",
                "gsrnamespace": 6, "gsrlimit": per_query, "prop": "imageinfo",
                "iiprop": "url|size|extmetadata", "iiurlwidth": 1600,
            }
            pages = (self.http.get(api, params=params, timeout=60).json().get("query") or {}).get("pages", {})
            for page in pages.values():
                title = page.get("title", "")
                if title in seen or not page.get("imageinfo"):
                    continue
                seen.add(title)
                info = page["imageinfo"][0]
                meta = info.get("extmetadata", {})
                lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
                if not is_public_domain(lic):
                    continue
                if min(info.get("width", 0), info.get("height", 0)) < MIN_SIDE // 2:
                    continue
                out.append(Picture(
                    source="commons", key=title,
                    title=_strip_html(meta.get("ObjectName", {}).get("value", "")) or title.removeprefix("File:").rsplit(".", 1)[0],
                    artist=_strip_html(meta.get("Artist", {}).get("value", "")) or "Unknown",
                    license=lic, page_url=info.get("descriptionurl", ""),
                    image_url=info.get("thumburl") or info["url"],
                ))
        return out

    # -- download -----------------------------------------------------------------
    def download(self, pic: Picture, folder: Path) -> Path | None:
        path = folder / pic.filename
        if path.exists():
            return path
        r = self.http.get(pic.image_url, timeout=120)
        if r.status_code != 200:
            return None
        try:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            return None
        if max(img.size) < MIN_SIDE:
            return None
        img.thumbnail((2000, 2000))
        img.save(path, "JPEG", quality=90)
        return path


def write_credits(folder: Path, pics: list[tuple[Picture, Path]]) -> Path:
    lines = ["# Background picture credits", "",
             "Every picture here is public domain or CC0. No credit is required, but this list records the sources.", "",
             "| File | Title | Artist | Licence | Source |", "| --- | --- | --- | --- | --- |"]
    for pic, path in pics:
        lines.append(f"| {path.name} | {pic.title} | {pic.artist} | {pic.license} | {pic.page_url} |")
    out = folder / "CREDITS.md"
    out.write_text("\n".join(lines) + "\n")
    return out


def fetch(folder: Path, limit: int = 40, fetcher: Fetcher | None = None, log=print) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    f = fetcher or Fetcher()
    candidates: list[Picture] = []
    for name, getter in (("The Met", f.met), ("Wikimedia Commons", f.commons)):
        try:
            got = getter()
            log(f"{name}: {len(got)} public-domain candidates")
            candidates += got
        except (requests.RequestException, ValueError) as e:
            log(f"{name}: skipped ({e})")
    saved: list[tuple[Picture, Path]] = []
    for pic in candidates:
        if len(saved) >= limit:
            break
        try:
            path = f.download(pic, folder)
        except requests.RequestException:
            path = None
        if path:
            saved.append((pic, path))
            log(f"  saved {path.name}")
    write_credits(folder, saved)
    return [p for _, p in saved]


def contact_sheet(folder: Path, out: Path, thumb: int = 240, cols: int = 6) -> Path:
    """One image with every background side by side, to review them quickly."""
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    rows = max(1, -(-len(files) // cols))
    sheet = Image.new("RGB", (cols * thumb, rows * thumb), "black")
    for i, p in enumerate(files):
        img = Image.open(p).convert("RGB")
        img.thumbnail((thumb, thumb))
        sheet.paste(img, ((i % cols) * thumb, (i // cols) * thumb))
    sheet.save(out)
    return out
