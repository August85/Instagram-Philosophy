"""Quote bank, topics, and reel recipes.

A recipe is everything needed to render and post one reel: the text shown on
screen and when, the visual theme, and the caption. Recipes live in
reels/batch-NN.yaml (100 per file) so the whole backlog is reviewable in
GitHub, while the rendered MP4s stay on the local machine.
"""
from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BATCH_SIZE = 100

# Colour themes: two gradient colours, text colour, accent colour.
THEMES: dict[str, dict[str, str]] = {
    "marble": {"c0": "0x2b2b2b", "c1": "0x6b6560", "text": "#F5F1EA", "accent": "#D9C7A3"},
    "midnight": {"c0": "0x0b1026", "c1": "0x283a6b", "text": "#EEF2FF", "accent": "#9FB4FF"},
    "ember": {"c0": "0x1f0d07", "c1": "0x7a2e12", "text": "#FFF3E6", "accent": "#FFB27A"},
    "forest": {"c0": "0x08160f", "c1": "0x2d5a40", "text": "#EFF7F0", "accent": "#A8D5B5"},
    "dusk": {"c0": "0x1a0f24", "c1": "0x6b3a5e", "text": "#FBEFF7", "accent": "#F2B5D4"},
    "ink": {"c0": "0x000000", "c1": "0x262626", "text": "#FFFFFF", "accent": "#BFBFBF"},
    "ocean": {"c0": "0x03161f", "c1": "0x135e6e", "text": "#ECFBFF", "accent": "#8FE3F2"},
    "sand": {"c0": "0x3b2c1a", "c1": "0x8a6d45", "text": "#FFF8EC", "accent": "#F3D9A4"},
}

FORMATS = ["quote", "hook_quote", "quote_reflection", "lesson", "hook_quote_cta"]


@dataclass
class Quote:
    id: str
    text: str
    author: str
    topic: str
    work: str = ""
    ref: str = ""  # verse or passage number, e.g. "2.47"
    speaker: str = ""  # who says it inside the text, e.g. Arjuna
    header: str = ""  # title for the "lesson" format
    hashtags: list[str] = field(default_factory=list)
    checked: bool = False

    @property
    def credit(self) -> str:
        """Short line shown under the quote: 'Bhagavad Gita 2.47' or 'Seneca'."""
        return f"{self.author} {self.ref}".strip()

    @property
    def source(self) -> str:
        """Longer source line for the caption."""
        parts = [self.credit]
        if self.work:
            parts.append(self.work)
        if self.speaker:
            parts.append(f"spoken by {self.speaker}")
        return ", ".join(parts)

    @property
    def group(self) -> str:
        """Reels from the same group are not posted back to back: the same
        author, or for a single book the same chapter."""
        chapter = self.ref.split(".")[0] if self.ref else ""
        return f"{self.author}:{chapter}"


@dataclass
class Segment:
    role: str  # hook | header | quote | author | reflection | cta
    text: str
    start: float
    end: float


@dataclass
class Recipe:
    id: str
    quote_id: str
    format: str
    theme: str
    duration: float
    segments: list[Segment]
    caption: str
    voiceover_text: str | None = None
    voiceover_start: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "quote_id": self.quote_id,
            "format": self.format,
            "theme": self.theme,
            "duration": round(self.duration, 2),
            "segments": [
                {"role": s.role, "text": s.text, "start": round(s.start, 2), "end": round(s.end, 2)}
                for s in self.segments
            ],
            "voiceover_text": self.voiceover_text,
            "voiceover_start": round(self.voiceover_start, 2),
            "caption": self.caption,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Recipe":
        return cls(
            id=str(d["id"]),
            quote_id=d["quote_id"],
            format=d["format"],
            theme=d["theme"],
            duration=float(d["duration"]),
            segments=[Segment(**s) for s in d["segments"]],
            caption=d["caption"],
            voiceover_text=d.get("voiceover_text"),
            voiceover_start=float(d.get("voiceover_start", 0.0)),
        )


def load_quotes(root: Path) -> list[Quote]:
    """Read content/quotes.yaml: either a plain list of quotes, or
    {defaults: {...}, quotes: [...]} where defaults fill in missing fields."""
    data = yaml.safe_load((root / "content" / "quotes.yaml").read_text())
    defaults: dict[str, Any] = {}
    if isinstance(data, dict):
        defaults = data.get("defaults", {}) or {}
        data = data.get("quotes", [])
    return [Quote(**{**defaults, **q}) for q in data]


def load_topics(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / "content" / "topics.yaml").read_text())


def validate_content(quotes: list[Quote], topics: dict[str, Any]) -> list[str]:
    problems = []
    ids = Counter(q.id for q in quotes)
    problems += [f"duplicate quote id {i}" for i, n in ids.items() if n > 1]
    for q in quotes:
        if q.topic not in topics["topics"]:
            problems.append(f"{q.id}: unknown topic '{q.topic}'")
    return problems


def reading_time(text: str) -> float:
    """Seconds a viewer needs to read a line comfortably."""
    words = len(text.split())
    return max(3.5, min(12.0, 1.5 + words * 0.36))


def _pick(options: list[str], key: str) -> str:
    h = int(hashlib.sha1(key.encode()).hexdigest(), 16)
    return options[h % len(options)]


def build_recipe(rid: str, quote: Quote, fmt: str, theme: str, variant: int, topics: dict[str, Any]) -> Recipe:
    topic = topics["topics"][quote.topic]
    defaults = topics["defaults"]
    hook = topic["hooks"][variant % len(topic["hooks"])]
    reflection = topic["reflections"][variant % len(topic["reflections"])]
    cta = _pick(defaults["ctas"], f"{rid}-cta")

    segs: list[Segment] = []
    t = 0.4
    quote_text = f"“{quote.text}”"
    if fmt in ("hook_quote", "hook_quote_cta"):
        segs.append(Segment("hook", hook, t, t + 2.6))
        t += 2.8
    if fmt == "lesson":
        segs.append(Segment("header", quote.header or f"A lesson from {quote.author}", t, t + 2.4))
        t += 2.6
    q_start = t
    q_len = reading_time(quote.text)
    segs.append(Segment("quote", quote_text, t, t + q_len + 1.2))
    segs.append(Segment("author", f"— {quote.credit}", t + 0.8, t + q_len + 1.2))
    t += q_len + 1.4
    if fmt == "quote_reflection":
        segs.append(Segment("reflection", reflection, t, t + 3.6))
        t += 3.8
    if fmt in ("lesson", "hook_quote_cta"):
        segs.append(Segment("cta", cta, t, t + 2.4))
        t += 2.6
    duration = max(7.0, t + 0.4)
    # Let the last text stay on screen until the end (the quote and its
    # author line together when they come last).
    segs[-1].end = duration
    if segs[-1].role == "author":
        segs[-2].end = duration

    # The first two bank hashtags always go on; two more rotate.
    tags = list(quote.hashtags or defaults["hashtags"])
    fixed, rest = tags[:2], tags[2:]
    random.Random(rid).shuffle(rest)
    hashtags = " ".join(dict.fromkeys(fixed + topic.get("hashtags", []) + rest[:2]))
    caption_parts = []
    if fmt in ("hook_quote", "hook_quote_cta"):
        caption_parts.append(hook)
    caption_parts.append(f"{quote_text}\n— {quote.source}")
    caption_parts.append(reflection)
    caption_parts.append(cta)
    caption_parts.append(hashtags)
    caption = "\n\n".join(caption_parts)

    return Recipe(
        id=rid,
        quote_id=quote.id,
        format=fmt,
        theme=theme,
        duration=duration,
        segments=segs,
        caption=caption,
        voiceover_text=quote.text,
        voiceover_start=q_start + 0.3,
    )


def plan_order(quotes: list[Quote], count: int, min_gap: int, seed: int = 7) -> list[Quote]:
    """Order quotes for `count` posts.

    Each quote is used about equally often, the same quote never comes back
    within `min_gap` posts, and two posts in a row never share a group (same
    author, or same chapter when every quote is from one book).
    """
    rng = random.Random(seed)
    uses = {q.id: 0 for q in quotes}
    last_pos: dict[str, int] = {}
    target = -(-count // len(quotes))  # ceil
    order: list[Quote] = []
    for pos in range(count):
        prev_group = order[-1].group if order else None
        gap = min_gap
        while True:
            spaced = [q for q in quotes if uses[q.id] < target and pos - last_pos.get(q.id, -10**9) > gap]
            cands = [q for q in spaced if q.group != prev_group] or spaced
            if cands or gap == 0:
                break
            gap //= 2
        if not cands:
            cands = [q for q in quotes if uses[q.id] < target] or quotes
        fewest = min(uses[q.id] for q in cands)
        best = [q for q in cands if uses[q.id] == fewest]
        q = rng.choice(best)
        uses[q.id] += 1
        last_pos[q.id] = pos
        order.append(q)
    return order


def generate_recipes(root: Path, count: int = 1000, seed: int = 7) -> list[Recipe]:
    quotes = load_quotes(root)
    topics = load_topics(root)
    problems = validate_content(quotes, topics)
    if problems:
        raise ValueError("Content problems:\n" + "\n".join(problems))
    gap = max(1, min(len(quotes) - 5, int(len(quotes) * 0.8)))
    order = plan_order(quotes, count, min_gap=gap, seed=seed)
    theme_names = list(THEMES)
    seen: Counter[str] = Counter()
    recipes = []
    for i, q in enumerate(order, start=1):
        n = seen[q.id]
        seen[q.id] += 1
        fmt = FORMATS[(n + int(hashlib.sha1(q.id.encode()).hexdigest(), 16)) % len(FORMATS)]
        theme = theme_names[(i * 3 + n) % len(theme_names)]
        recipes.append(build_recipe(f"{i:04d}", q, fmt, theme, n, topics))
    return recipes


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)


def write_recipes(root: Path, recipes: list[Recipe]) -> list[Path]:
    out_dir = root / "reels"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("batch-*.yaml"):
        old.unlink()
    paths = []
    for b in range(0, len(recipes), BATCH_SIZE):
        chunk = recipes[b:b + BATCH_SIZE]
        path = out_dir / f"batch-{b // BATCH_SIZE + 1:02d}.yaml"
        header = f"# Reels {chunk[0].id} to {chunk[-1].id}. Generated by `philoreels generate`; edit freely.\n"
        body = yaml.dump([r.to_dict() for r in chunk], Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
        path.write_text(header + body)
        paths.append(path)
    return paths


_RECIPE_CACHE: dict[tuple, list[Recipe]] = {}


def load_recipes(root: Path) -> list[Recipe]:
    """All recipes in order. Cached until a batch file changes."""
    paths = sorted((root / "reels").glob("batch-*.yaml"))
    key = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    if key not in _RECIPE_CACHE:
        _RECIPE_CACHE.clear()
        _RECIPE_CACHE[key] = _read_recipes(paths)
    return list(_RECIPE_CACHE[key])


def _read_recipes(paths: list[Path]) -> list[Recipe]:
    recipes = []
    for path in paths:
        for d in yaml.safe_load(path.read_text()) or []:
            if d.get("skip"):
                continue
            recipes.append(Recipe.from_dict(d))
    return recipes
