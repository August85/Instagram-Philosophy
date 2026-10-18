import json
import shutil
import subprocess

import pytest

from philoreels import render
from philoreels.content import load_recipes
from conftest import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                          "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_renders_gradient_reel(cfg):
    recipe = load_recipes(ROOT)[0]
    out = render.render(cfg, recipe)
    info = probe(out)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert kinds["video"]["codec_name"] == "h264"
    assert (kinds["video"]["width"], kinds["video"]["height"]) == (270, 480)
    assert kinds["audio"]["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - recipe.duration) < 0.3


def test_renders_with_image_background_and_music(cfg, tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    (assets / "backgrounds").mkdir(parents=True)
    (assets / "music").mkdir()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360",
                    "-frames:v", "1", str(assets / "backgrounds" / "bg.jpg")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=3",
                    str(assets / "music" / "m.mp3")], check=True)
    real_resolve = cfg.resolve
    monkeypatch.setattr(cfg, "resolve", lambda p: (tmp_path / p) if p.startswith("assets/") and "fonts" not in p else real_resolve(p))
    recipe = load_recipes(ROOT)[1]
    out = render.render(cfg, recipe)
    assert {s["codec_type"] for s in probe(out)["streams"]} == {"video", "audio"}
