"""Local state kept in the data folder (never committed):

  state.json     go-live date, pause flag, daily cap, failure count
  token.json     Instagram access token and account id
  posted.jsonl   one line per published reel
  failed.jsonl   one line per failed attempt
  videos/        rendered MP4s
  logs/          output of the scheduled job
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


class Store:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "logs").mkdir(exist_ok=True)

    # -- json helpers --------------------------------------------------------
    def _read(self, name: str, default: Any) -> Any:
        p = self.dir / name
        if not p.exists():
            return default
        return json.loads(p.read_text())

    def _write(self, name: str, data: Any) -> None:
        p = self.dir / name
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(p)

    # -- state ---------------------------------------------------------------
    @property
    def state(self) -> dict[str, Any]:
        return self._read("state.json", {})

    def update_state(self, **changes: Any) -> dict[str, Any]:
        s = self.state
        s.update(changes)
        self._write("state.json", s)
        return s

    @property
    def start_date(self) -> date | None:
        v = self.state.get("start_date")
        return date.fromisoformat(v) if v else None

    # -- token ---------------------------------------------------------------
    @property
    def token(self) -> dict[str, Any]:
        return self._read("token.json", {})

    def save_token(self, **data: Any) -> None:
        t = self.token
        t.update(data)
        self._write("token.json", t)
        (self.dir / "token.json").chmod(0o600)

    # -- logs ----------------------------------------------------------------
    def _append(self, name: str, row: dict[str, Any]) -> None:
        with (self.dir / name).open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def _rows(self, name: str) -> list[dict[str, Any]]:
        p = self.dir / name
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def record_post(self, recipe_id: str, media_id: str, container_id: str, permalink: str | None,
                    when: datetime) -> None:
        self._append("posted.jsonl", {"recipe_id": recipe_id, "media_id": media_id,
                                      "container_id": container_id, "permalink": permalink,
                                      "posted_at": when.isoformat(timespec="seconds")})

    def record_failure(self, recipe_id: str, error: str, when: datetime) -> None:
        self._append("failed.jsonl", {"recipe_id": recipe_id, "error": error,
                                      "at": when.isoformat(timespec="seconds")})

    def posts(self) -> list[dict[str, Any]]:
        return self._rows("posted.jsonl")

    def posted_ids(self) -> set[str]:
        return {r["recipe_id"] for r in self.posts()}

    def posts_on(self, day: date) -> list[dict[str, Any]]:
        return [r for r in self.posts() if datetime.fromisoformat(r["posted_at"]).date() == day]

    def last_post_time(self) -> datetime | None:
        rows = self.posts()
        return datetime.fromisoformat(rows[-1]["posted_at"]) if rows else None

    def failures_for(self, recipe_id: str) -> int:
        return sum(1 for r in self._rows("failed.jsonl") if r["recipe_id"] == recipe_id)

    # -- lock ----------------------------------------------------------------
    @contextmanager
    def lock(self) -> Iterator[bool]:
        """Stop two scheduled runs overlapping. Yields False if already locked."""
        p = self.dir / "tick.lock"
        if p.exists() and (datetime.now().timestamp() - p.stat().st_mtime) < 3600:
            yield False
            return
        p.write_text(str(datetime.now()))
        try:
            yield True
        finally:
            p.unlink(missing_ok=True)
