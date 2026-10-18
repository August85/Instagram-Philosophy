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


def test_prepare_background_shows_whole_picture(cfg, tmp_path):
    from PIL import Image
    src = tmp_path / "wide.jpg"
    Image.new("RGB", (1600, 900), "red").save(src)
    out = render.prepare_background(cfg, src)
    img = Image.open(out)
    w, h = cfg.get("render.width"), cfg.get("render.height")
    assert img.size == (int(w * 1.1), int(h * 1.1))
    # The centre is the sharp red picture; the top edge is the darker blurred fill.
    cx, cy = img.width // 2, img.height // 2
    assert img.getpixel((cx, cy))[0] > 200
    assert img.getpixel((cx, 2))[0] < 200
    assert render.prepare_background(cfg, src) == out  # cached


def test_spoken_text_can_read_the_reference():
    from philoreels.content import Segment
    assert render.spoken_text(Segment("author", "— Bhagavad Gita 2.47", 0, 1), read_reference=True) == "Bhagavad Gita, chapter 2, verse 47"
    assert render.spoken_text(Segment("author", "— Bhagavad Gita 4.7-8", 0, 1), read_reference=True) == "Bhagavad Gita, chapter 4, verses 7 to 8"
    assert render.spoken_text(Segment("quote", "“Act, and let go.”", 0, 1)) == "Act, and let go."


def test_voiceover_reads_every_line_and_stretches_timing(cfg, monkeypatch):
    """Fake `say`: a tone two-thirds of a second per word, so every line is longer than its slot."""
    def fake_speak(cfg, text, out):
        out = out.with_suffix(".wav")
        secs = 0.66 * len(text.split())
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f=300:d={secs}",
                        str(out)], check=True)
        return out
    monkeypatch.setattr(render, "speak", fake_speak)
    monkeypatch.setattr(render, "voiceover_enabled", lambda cfg: True)
    recipe = next(r for r in load_recipes(ROOT) if r.format == "hook_quote_cta")
    out = render.render(cfg, recipe, force=True)
    timing = json.loads(render.timing_path(out).read_text())
    assert timing["voiceover"] and timing["duration"] > recipe.duration
    assert abs(float(probe(out)["format"]["duration"]) - timing["duration"]) < 0.3
    # Retiming keeps lines in order, each on screen until its words are read.
    clips = [(None, 0.66 * len(render.spoken_text(s).split())) for s in recipe.segments]
    new, placed = render.retime(recipe, clips)
    spoken = [(seg, start, length) for seg, (_, start), (_, length) in zip(new.segments, placed, clips) if length]
    assert len(spoken) == len(recipe.segments) - 1  # everything but the verse reference
    for seg, start, length in spoken:
        assert seg.start <= start and start + length < seg.end <= new.duration
    for (_, a, la), (_, b, _) in zip(spoken, spoken[1:]):
        assert b >= a + la
