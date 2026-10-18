"""Download free background pictures for the reels.

Only pictures that need no permission, credit or fee are kept:

- Openverse (openverse.org): photos and illustrations marked CC0 or public domain.
- Wikimedia Commons, filtered to "Public domain" / CC0 files.
- Pixabay (Pixabay Content License), when PIXABAY_API_KEY is set in .env.
- The Met Open Access collection (CC0), for paintings (not used by default).

What to search for is set under `images:` in config.yaml. Pictures with a
human face in them are dropped (see faces.py). Everything is saved to
assets/backgrounds/ with a CREDITS.md listing where each picture came from.
Delete any you don't like; reels pick from whatever is left.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from itertools import zip_longest
from typing import Any, Iterable

import requests
from PIL import Image

USER_AGENT = "philoreels/0.1 (Instagram philosophy reels; https://github.com/August85/Instagram-Philosophy)"

# v1.1 search ranks loose matches (e.g. the printmaker Krishna Reddy) highly,
# so the queries name Indian painting explicitly.
MET_QUERIES = ["Krishna painting India", "Krishna and Arjuna", "Bhagavata Purana", "Radha Krishna painting",
               "Vishnu painting India", "Mahabharata painting"]
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
        # requests.Session already carries a "python-requests" User-Agent, which
        # Wikimedia rejects with 403, so always overwrite it.
        self.http.headers["User-Agent"] = USER_AGENT

    # -- The Met ---------------------------------------------------------------
    def met(self, queries: Iterable[str] = MET_QUERIES, per_query: int = 60, delay: float = 0.5) -> list[Picture]:
        base = "https://collectionapi.metmuseum.org/public/collection/v1"
        # v1/search was retired on 2026-10-01; v1.1/search is paginated.
        search = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
        seen: set[int] = set()
        out: list[Picture] = []
        for q in queries:
            r = self.http.get(search, params={"q": q, "hasImages": "true", "limit": per_query, "offset": 0}, timeout=60)
            ids = (r.json().get("objectIDs") or [])[:per_query]
            for oid in ids:
                if oid in seen:
                    continue
                seen.add(oid)
                time.sleep(delay)
                r = self.http.get(f"{base}/objects/{oid}", timeout=60)
                if r.status_code == 403:
                    # The Met's bot protection answers rapid runs with 403 HTML;
                    # keep what we have rather than losing every result.
                    return out
                if r.status_code != 200:
                    continue
                obj = r.json()
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

    # -- Openverse ---------------------------------------------------------------
    def openverse(self, queries: Iterable[str], per_query: int = 20) -> list[Picture]:
        api = "https://api.openverse.org/v1/images/"
        out: list[Picture] = []
        seen: set[str] = set()
        for q in queries:
            r = self.http.get(api, params={"q": q, "license": "cc0,pdm", "page_size": per_query, "mature": "false"},
                              timeout=60)
            if r.status_code != 200:
                continue
            for item in r.json().get("results", []):
                key = item.get("id", "")
                if not key or key in seen or item.get("mature"):
                    continue
                if min(item.get("width") or 0, item.get("height") or 0) and \
                        min(item.get("width") or 0, item.get("height") or 0) < MIN_SIDE:
                    continue
                seen.add(key)
                lic = (item.get("license") or "").lower()
                out.append(Picture(
                    source="openverse", key=key, title=item.get("title") or q,
                    artist=item.get("creator") or "Unknown",
                    license="CC0" if lic == "cc0" else "Public domain",
                    page_url=item.get("foreign_landing_url") or "", image_url=item["url"],
                ))
        return out

    # -- Pixabay -------------------------------------------------------------------
    def pixabay(self, queries: Iterable[str], per_query: int = 20, key: str | None = None) -> list[Picture]:
        key = key or os.environ.get("PIXABAY_API_KEY")
        if not key:
            return []
        out: list[Picture] = []
        seen: set[int] = set()
        for q in queries:
            r = self.http.get("https://pixabay.com/api/", params={
                "key": key, "q": q, "safesearch": "true", "per_page": max(3, min(per_query, 200)),
                "min_width": MIN_SIDE, "min_height": MIN_SIDE}, timeout=60)
            if r.status_code != 200:
                continue
            for hit in r.json().get("hits", []):
                if hit["id"] in seen:
                    continue
                seen.add(hit["id"])
                out.append(Picture(
                    source="pixabay", key=str(hit["id"]), title=hit.get("tags") or q,
                    artist=hit.get("user") or "Unknown", license="Pixabay Content License",
                    page_url=hit.get("pageURL", ""), image_url=hit.get("largeImageURL") or hit["webformatURL"],
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
             "Every picture here is public domain, CC0 or under the Pixabay Content License. No credit is required, "
             "but this list records the sources.", "",
             "| File | Title | Artist | Licence | Source |", "| --- | --- | --- | --- | --- |"]
    for pic, path in pics:
        lines.append(f"| {path.name} | {pic.title} | {pic.artist} | {pic.license} | {pic.page_url} |")
    out = folder / "CREDITS.md"
    out.write_text("\n".join(lines) + "\n")
    return out


DEFAULT_QUERIES = ["Indian village", "rural India landscape", "paddy field India", "village road India"]


def fetch(folder: Path, limit: int = 40, fetcher: Fetcher | None = None, log=print,
          sources: Iterable[str] = ("openverse", "commons", "pixabay"), queries: Iterable[str] | None = None,
          reject_faces: bool = True, face_counter=None) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    f = fetcher or Fetcher()
    queries = list(queries or DEFAULT_QUERIES)
    getters = {
        "openverse": lambda: f.openverse(queries),
        "commons": lambda: f.commons(queries),
        "pixabay": lambda: f.pixabay(queries),
        "met": lambda: f.met(),
    }
    candidates: list[Picture] = []
    for name in sources:
        try:
            got = getters[name]()
            log(f"{name}: {len(got)} free candidates")
            candidates += got
        except (requests.RequestException, ValueError) as e:
            log(f"{name}: skipped ({e})")
    # Take turns between sources and queries so one source doesn't fill the folder.
    by_source: dict[str, list[Picture]] = {}
    for pic in candidates:
        by_source.setdefault(pic.source, []).append(pic)
    mixed = [p for group in zip_longest(*by_source.values()) for p in group if p]

    from . import faces
    count_faces = face_counter or faces.count_faces
    saved: list[tuple[Picture, Path]] = []
    rejected = 0
    for start in range(0, len(mixed), 12):
        if len(saved) >= limit:
            break
        batch: list[tuple[Picture, Path]] = []
        for pic in mixed[start:start + 12]:
            try:
                path = f.download(pic, folder)
            except requests.RequestException:
                path = None
            if path:
                batch.append((pic, path))
        if reject_faces and batch:
            counts = count_faces([p for _, p in batch])
            if counts is None:
                log("  face check unavailable on this machine; review the pictures by eye")
                reject_faces = False
            else:
                kept = []
                for pic, path in batch:
                    if counts.get(str(path), 0) != 0:
                        path.unlink(missing_ok=True)
                        rejected += 1
                    else:
                        kept.append((pic, path))
                batch = kept
        for pic, path in batch:
            if len(saved) >= limit:
                path.unlink(missing_ok=True)
                continue
            saved.append((pic, path))
            log(f"  saved {path.name}")
    if rejected:
        log(f"Dropped {rejected} pictures that show a face.")
    existing = _read_credits(folder)
    write_credits(folder, existing + [s for s in saved if s[1].name not in {e[1].name for e in existing}])
    return [p for _, p in saved]


def _read_credits(folder: Path) -> list[tuple[Picture, Path]]:
    """Keep credit rows for pictures from earlier runs that are still in the folder."""
    path = folder / "CREDITS.md"
    rows: list[tuple[Picture, Path]] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and (folder / cells[0]).exists() and cells[0] != "File":
            name, title, artist, lic, url = cells
            rows.append((Picture("", name, title, artist, lic, url, ""), folder / name))
    return rows


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
