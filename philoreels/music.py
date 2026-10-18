"""Make calm, uplifting background music on this machine.

Each track is a tanpura-style drone with a soft pad and a slow bansuri-like
(flute) melody in a pentatonic raga, with a little reverb. Because the music
is synthesised here, it is free of any copyright claims. Your own royalty-free
tracks can sit in the same folder (assets/music); reels pick from everything
there.
"""
from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 44100


@dataclass
class TrackSpec:
    name: str
    sa: float  # tonic frequency in Hz
    scale: list[int]  # semitones above Sa
    tempo: float  # seconds per melody beat
    seed: int


# Bhupali and Durga are bright, hopeful ragas; Shuddha Sarang-like and a
# minor pentatonic give a few calmer moods for variety.
SPECS = [
    TrackSpec("bhupali-dawn", 138.59, [0, 2, 4, 7, 9], 0.95, 1),
    TrackSpec("durga-light", 146.83, [0, 2, 5, 7, 9], 0.90, 2),
    TrackSpec("bhupali-river", 130.81, [0, 2, 4, 7, 9], 1.05, 3),
    TrackSpec("durga-temple", 155.56, [0, 2, 5, 7, 9], 1.00, 4),
    TrackSpec("sarang-noon", 146.83, [0, 2, 5, 7, 11], 0.95, 5),
    TrackSpec("bhupali-sky", 164.81, [0, 2, 4, 7, 9], 0.85, 6),
    TrackSpec("malkauns-night", 138.59, [0, 3, 5, 8, 10], 1.10, 7),
    TrackSpec("durga-hope", 130.81, [0, 2, 5, 7, 9], 0.92, 8),
]


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-3), 0, 1)
    return a * np.exp(-t / decay)


def _tanpura(duration: float, sa: float, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(int(duration * SR))
    pattern = [sa * 1.5 / 2, sa, sa, sa / 2]  # Pa (lower), Sa, Sa, Sa (lower)
    step, i, t = 1.25, 0, 0.0
    pluck_len = int(5.0 * SR)
    tt = np.arange(pluck_len) / SR
    while t < duration:
        f = pattern[i % 4] * (1 + rng.normal(0, 0.0007))
        tone = np.zeros(pluck_len)
        for h in range(1, 14):
            # Upper harmonics swell and fade slowly: the buzzing "jawari" shimmer.
            shimmer = 0.5 + 0.5 * np.sin(2 * np.pi * (0.35 + 0.05 * h) * tt + h)
            tone += (1.0 / h) * (0.6 + 0.4 * shimmer) * np.sin(2 * np.pi * f * h * tt + rng.uniform(0, 6.28))
        tone *= _env(pluck_len, 0.01, 2.2)
        s = int(t * SR)
        e = min(len(out), s + pluck_len)
        out[s:e] += tone[: e - s]
        t += step
        i += 1
    return out * 0.10


def _pad(duration: float, sa: float, scale: list[int]) -> np.ndarray:
    n = int(duration * SR)
    t = np.arange(n) / SR
    third = scale[2] if len(scale) > 2 else 4
    freqs = [sa, sa * 2 ** (7 / 12), sa * 2 ** (third / 12) * 2, sa * 2]
    out = np.zeros(n)
    for k, f in enumerate(freqs):
        for detune in (-0.12, 0.12):
            out += np.sin(2 * np.pi * (f + detune) * t) + 0.25 * np.sin(2 * np.pi * 2 * (f + detune) * t)
    swell = 0.6 + 0.4 * np.sin(2 * np.pi * t / 11.0)
    fade = np.clip(t / 4.0, 0, 1)
    return out * swell * fade * 0.012


def _flute(duration: float, sa: float, scale: list[int], tempo: float, rng: np.random.Generator) -> np.ndarray:
    n = int(duration * SR)
    out = np.zeros(n)
    # Melody notes around the middle octave (one octave above the drone's Sa).
    notes = [s + 12 * o for o in (1, 2) for s in scale] + [24]
    idx = 2
    t = 3.0
    while t < duration - 3:
        phrase = rng.integers(3, 7)
        for _ in range(phrase):
            idx = int(np.clip(idx + rng.choice([-2, -1, -1, 0, 1, 1, 2]), 0, len(notes) - 1))
            beats = rng.choice([1, 1, 2, 2, 3, 4])
            length = beats * tempo
            f = sa * 2 ** (notes[idx] / 12)
            m = int(length * SR)
            tt = np.arange(m) / SR
            vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * tt) * np.clip(tt / 0.6, 0, 1)
            phase = 2 * np.pi * np.cumsum(f * vib) / SR
            tone = np.sin(phase) + 0.18 * np.sin(2 * phase) + 0.05 * np.sin(3 * phase)
            breath = np.convolve(rng.normal(0, 1, m), np.ones(24) / 24, mode="same") * 0.05
            env = np.clip(tt / 0.12, 0, 1) * np.clip((length - tt) / 0.35, 0, 1)
            s = int(t * SR)
            e = min(n, s + m)
            out[s:e] += ((tone + breath) * env)[: e - s]
            t += length
            if t >= duration - 3:
                break
        t += rng.choice([1.5, 2.0, 3.0]) * tempo
    return out * 0.07


def _lowpass(x: np.ndarray, cutoff: float = 4500.0) -> np.ndarray:
    """Gentle roll-off of the highs so nothing sounds harsh or hissy."""
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= 1 / (1 + (freqs / cutoff) ** 4)
    return np.fft.irfft(spec, len(x))


def _reverb(x: np.ndarray, seconds: float = 2.8, mix: float = 0.35) -> np.ndarray:
    n = int(seconds * SR)
    rng = np.random.default_rng(99)
    ir = rng.normal(0, 1, n) * np.exp(-np.arange(n) / (SR * seconds / 6))
    ir /= np.sqrt(np.sum(ir ** 2))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return (1 - mix) * x + mix * wet


def synth(spec: TrackSpec, duration: float = 75.0) -> np.ndarray:
    """Return a stereo float array in [-1, 1]."""
    rng = np.random.default_rng(spec.seed)
    drone = _tanpura(duration, spec.sa, rng)
    pad = _pad(duration, spec.sa, spec.scale)
    flute = _flute(duration, spec.sa, spec.scale, spec.tempo, rng)
    left = _lowpass(_reverb(drone * 1.0 + pad + flute * 0.9))
    right = _lowpass(_reverb(drone * 0.9 + pad + flute * 1.0))
    st = np.stack([left, right], axis=1)
    t = np.arange(len(st)) / SR
    fade = np.clip(t / 2.0, 0, 1) * np.clip((duration - t) / 3.0, 0, 1)
    st *= fade[:, None]
    return st / (np.max(np.abs(st)) + 1e-9) * 0.8


def write_track(spec: TrackSpec, folder: Path, duration: float = 75.0) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / f"generated-{spec.name}.wav"
    data = (synth(spec, duration) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    out = wav.with_suffix(".m4a")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
                    "-c:a", "aac", "-b:a", "160k", str(out)], check=True)
    wav.unlink()
    return out


def make_library(folder: Path, count: int = len(SPECS), duration: float = 75.0) -> list[Path]:
    return [write_track(spec, folder, duration) for spec in SPECS[:count]]
