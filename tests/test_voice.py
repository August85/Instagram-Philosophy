import json

import pytest

from philoreels import render, voice
from philoreels.content import Segment


class FakeResp:
    def __init__(self, status=200, content=b"ID3fake", data=None):
        self.status_code, self.content, self._data, self.text = status, content, data, "err"

    def json(self):
        return self._data


class FakeHTTP:
    def __init__(self):
        self.posts = []

    def post(self, url, params=None, headers=None, json=None, timeout=None):
        self.posts.append((url, headers, json))
        return FakeResp()

    def get(self, url, headers=None, timeout=None):
        return FakeResp(data={"voices": [{"voice_id": "v123", "name": "AK clone", "category": "cloned"}]})


def eleven_cfg(cfg, monkeypatch, voice_id="v123"):
    cfg.raw["render"]["tts"] = "elevenlabs"
    cfg.raw["render"]["elevenlabs"]["voice_id"] = voice_id
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    return cfg


def test_reference_is_shown_but_not_read():
    seg = Segment("author", "— भगवद्गीता 2.47", 0, 1)
    assert render.spoken_text(seg) == ""
    assert render.spoken_text(Segment("quote", "“कर्म करो।”", 0, 1)) == "कर्म करो।"


def test_unread_lines_get_no_clip_and_keep_their_time(cfg):
    from philoreels.content import load_recipes
    from conftest import ROOT
    recipe = load_recipes(ROOT)[0]
    clips = [(None, 0.0) if s.role == "author" else ("clip", 2.0) for s in recipe.segments]
    new, placed = render.retime(recipe, clips)
    assert len(placed) == len(recipe.segments)
    author = next(s for s in new.segments if s.role == "author")
    quote = next(s for s in new.segments if s.role == "quote")
    assert author.end == quote.end


def test_elevenlabs_request_and_cache(cfg, monkeypatch, tmp_path):
    eleven_cfg(cfg, monkeypatch)
    http = FakeHTTP()
    monkeypatch.setattr(voice, "requests", http)
    a = voice.speak(cfg, "कर्म करो।", tmp_path / "x.aiff")
    b = voice.speak(cfg, "कर्म करो।", tmp_path / "y.aiff")
    assert a == b and a.read_bytes() == b"ID3fake" and a.suffix == ".mp3"
    assert len(http.posts) == 1  # the second call came from the cache
    url, headers, body = http.posts[0]
    assert url.endswith("/text-to-speech/v123") and headers["xi-api-key"] == "sk-test"
    assert body["text"] == "कर्म करो।" and body["model_id"] == "eleven_multilingual_v2"
    voice.speak(cfg, "फल की चिंता मत करो।", tmp_path / "z.aiff")
    assert len(http.posts) == 2


def test_elevenlabs_needs_key_and_voice(cfg, monkeypatch, tmp_path):
    eleven_cfg(cfg, monkeypatch, voice_id=None)
    with pytest.raises(voice.VoiceError, match="voice_id"):
        voice.speak(cfg, "नमस्ते", tmp_path / "x.aiff")
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    with pytest.raises(voice.VoiceError, match="ELEVENLABS_API_KEY"):
        voice.speak(cfg, "नमस्ते", tmp_path / "x.aiff")


def test_list_voices(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test")
    monkeypatch.setattr(voice, "requests", FakeHTTP())
    assert voice.list_elevenlabs_voices()[0] == {"voice_id": "v123", "name": "AK clone", "category": "cloned"}


def test_short_voice_shortens_the_pause():
    """A quick reader no longer leaves the verse up for seconds of silence."""
    from philoreels.content import load_recipes
    from conftest import ROOT
    recipe = load_recipes(ROOT)[0]  # quote, reference, reflection
    clips = [(None, 0.0) if s.role == "author" else ("clip", 2.0) for s in recipe.segments]
    new, placed = render.retime(recipe, clips)
    quote = next(s for s in new.segments if s.role == "quote")
    author = next(s for s in new.segments if s.role == "author")
    q_voice = next(t for (c, t), s in zip(placed, new.segments) if s.role == "quote")
    assert abs(quote.end - (q_voice + 2.0 + 1.5)) < 1e-6
    assert quote.start < author.start < quote.end and author.end == quote.end
    assert new.duration < recipe.duration
