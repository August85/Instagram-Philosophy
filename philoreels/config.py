"""Load config.yaml and .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data_dir(self) -> Path:
        override = os.environ.get("PHILOREELS_DATA_DIR")
        path = Path(override or self.get("paths.data_dir", "~/PhiloReels")).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.root / p

    def font(self, kind: str) -> str:
        for candidate in self.get(f"render.fonts.{kind}", []) or []:
            p = self.resolve(candidate)
            if p.exists():
                return str(p)
        raise FileNotFoundError(
            f"No font found for '{kind}'. Add a font file to assets/fonts or edit render.fonts in config.yaml."
        )


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(root: Path | None = None) -> Config:
    root = root or Path(os.environ.get("PHILOREELS_ROOT", REPO_ROOT))
    load_dotenv(root / ".env")
    raw = yaml.safe_load((root / "config.yaml").read_text()) or {}
    return Config(raw=raw, root=root)
