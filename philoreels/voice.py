"""Text-to-speech for the voiceover.

Two engines:

- say: the voices built into macOS (free, offline). The first name in
  render.voice that the Mac has is used.
- elevenlabs: an ElevenLabs voice, such as a clone of your own voice. Needs
  ELEVENLABS_API_KEY in .env and the voice's id in render.elevenlabs.voice_id
  (or ELEVENLABS_VOICE_ID in .env).

ElevenLabs audio is cached in <data_dir>/voice-cache by text and settings, so
each line is paid for once even though verses come back in many reels.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import requests

from .config import Config

ELEVENLABS_API = "https://api.elevenlabs.io/v1"


class VoiceError(RuntimeError):
    pass


def engine(cfg: Config) -> str:
    return cfg.get("render.tts", "say")


def available(cfg: Config) -> bool:
    if engine(cfg) == "elevenlabs":
        return True  # missing settings raise a clear error when speaking
    return shutil.which("say") is not None


# -- macOS say -------------------------------------------------------------------
_VOICE_CACHE: dict[str, str] = {}


def pick_voice(cfg: Config) -> str:
    """First voice from render.voice (a name or a list) that this Mac has."""
    wanted = cfg.get("render.voice", "Daniel")
    wanted = [wanted] if isinstance(wanted, str) else list(wanted)
    key = "|".join(wanted)
    if key not in _VOICE_CACHE:
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
            have = {re.split(r"\s{2,}", line.strip())[0] for line in out.splitlines() if line.strip()}
        except (OSError, subprocess.CalledProcessError):
            have = set()
        _VOICE_CACHE[key] = next((v for v in wanted if v in have), wanted[-1])
    return _VOICE_CACHE[key]


def _say(cfg: Config, text: str, out: Path) -> Path:
    out = out.with_suffix(".aiff")
    rate = str(cfg.get("render.voice_rate", 160))
    subprocess.run(["say", "-v", pick_voice(cfg), "-r", rate, "-o", str(out), text], check=True)
    return out


# -- ElevenLabs ------------------------------------------------------------------
def _eleven_settings(cfg: Config) -> dict:
    s = cfg.get("render.elevenlabs", {}) or {}
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID") or s.get("voice_id")
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("render.tts is 'elevenlabs' but ELEVENLABS_API_KEY is not set in .env")
    if not voice_id:
        raise VoiceError("Set render.elevenlabs.voice_id in config.yaml (run `philoreels voices` to list your voices)")
    body = {
        "model_id": s.get("model", "eleven_multilingual_v2"),
        "voice_settings": {
            "stability": s.get("stability", 0.5),
            "similarity_boost": s.get("similarity_boost", 0.8),
            "style": s.get("style", 0.0),
            "use_speaker_boost": True,
            "speed": s.get("speed", 0.9),
        },
    }
    if s.get("language_code"):
        body["language_code"] = s["language_code"]
    return {"key": key, "voice_id": voice_id, "body": body}


def _elevenlabs(cfg: Config, text: str, out: Path, session: requests.Session | None = None) -> Path:
    st = _eleven_settings(cfg)
    payload = {"text": text, **st["body"]}
    digest = hashlib.sha1(json.dumps([st["voice_id"], payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache = cfg.data_dir / "voice-cache"
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / f"{digest[:20]}.mp3"
    if not cached.exists():
        http = session or requests
        r = http.post(f"{ELEVENLABS_API}/text-to-speech/{st['voice_id']}",
                      params={"output_format": "mp3_44100_128"},
                      headers={"xi-api-key": st["key"], "Accept": "audio/mpeg"},
                      json=payload, timeout=120)
        if r.status_code != 200:
            raise VoiceError(f"ElevenLabs returned {r.status_code}: {r.text[:300]}")
        tmp = cached.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(cached)
    return cached


def list_elevenlabs_voices(session: requests.Session | None = None) -> list[dict]:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set in .env")
    r = (session or requests).get(f"{ELEVENLABS_API}/voices", headers={"xi-api-key": key}, timeout=60)
    if r.status_code != 200:
        raise VoiceError(f"ElevenLabs returned {r.status_code}: {r.text[:300]}")
    return [{"voice_id": v["voice_id"], "name": v.get("name", ""), "category": v.get("category", "")}
            for v in r.json().get("voices", [])]


def speak(cfg: Config, text: str, out: Path) -> Path:
    """Write speech for `text` and return the audio file (its suffix may differ from out's)."""
    if engine(cfg) == "elevenlabs":
        return _elevenlabs(cfg, text, out)
    return _say(cfg, text, out)
