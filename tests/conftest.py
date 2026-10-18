import copy
from pathlib import Path

import pytest

from philoreels.config import Config, load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Real config.yaml, small video size, data folder in a temp dir."""
    monkeypatch.setenv("PHILOREELS_DATA_DIR", str(tmp_path / "data"))
    for var in ("IG_ACCESS_TOKEN", "IG_USER_ID", "FB_APP_ID", "FB_APP_SECRET"):
        monkeypatch.delenv(var, raising=False)
    base = load_config(ROOT)
    raw = copy.deepcopy(base.raw)
    raw["render"].update(width=270, height=480, fps=15, preset="ultrafast", render_ahead=2, voiceover=False, tts="say")
    return Config(raw=raw, root=ROOT)
