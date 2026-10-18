"""Render a recipe into a 1080x1920 MP4 with FFmpeg and Pillow.

Text is drawn with Pillow into transparent PNGs (one per segment) and faded
in and out over a background with FFmpeg. The background is one of your own
clips or images from assets/backgrounds, or a slowly moving gradient when that
folder is empty. Music comes from assets/music. The voiceover reads every line aloud with
the macOS `say` command, and the timing stretches to fit the voice.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import Config
from .content import THEMES, Recipe, Segment

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}


class RenderError(RuntimeError):
    pass


def _stable_index(key: str, n: int) -> int:
    return int(hashlib.sha1(key.encode()).hexdigest(), 16) % n


def _files(folder: Path, exts: set[str]) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in exts and not p.name.startswith("."))


def _duration(path: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, check=True)
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        line = ""
        for word in words:
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _fit(text: str, font_path: str, size: int, min_size: int, max_width: int, max_height: int):
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    while True:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(probe, text, font, max_width)
        line_h = int(size * 1.32)
        height = line_h * len(lines)
        if height <= max_height or size <= min_size:
            return font, lines, line_h, height
        size -= 2


def draw_text_layer(
    size: tuple[int, int],
    text: str,
    font_path: str,
    font_size: int,
    color: str,
    center_y: int | None = None,
    top_y: int | None = None,
    max_width: int = 880,
    max_height: int = 1100,
    min_size: int = 40,
) -> tuple[Image.Image, int]:
    """Return a transparent layer with centred, wrapped text and the text's bottom y."""
    w, h = size
    font, lines, line_h, block_h = _fit(text, font_path, font_size, min_size, max_width, max_height)
    y0 = top_y if top_y is not None else (center_y or h // 2) - block_h // 2
    text_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    d_text, d_shadow = ImageDraw.Draw(text_layer), ImageDraw.Draw(shadow)
    for i, line in enumerate(lines):
        lw = d_text.textlength(line, font=font)
        x, y = (w - lw) / 2, y0 + i * line_h
        # A soft dark glow around the letters keeps them readable on bright pictures.
        d_shadow.text((x, y + 3), line, font=font, fill=(0, 0, 0, 235), stroke_width=max(3, font.size // 14),
                      stroke_fill=(0, 0, 0, 235))
        d_text.text((x, y), line, font=font, fill=color)
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    return Image.alpha_composite(shadow, text_layer), y0 + block_h


def _save_cropped(img: Image.Image, path: Path) -> tuple[int, int]:
    """Save only the area that has text (much faster to overlay) and return its offset."""
    box = img.getbbox() or (0, 0, 2, 2)
    x0, y0 = box[0] - box[0] % 2, box[1] - box[1] % 2
    img.crop((x0, y0, box[2], box[3])).save(path)
    return x0, y0


def prepare_background(cfg: Config, src: Path, margin: float = 1.1, min_fill: float = 0.6) -> Path:
    """Fit a picture into a vertical frame without cutting it off.

    The picture is shown whole, as wide as the frame, over a blurred and
    darkened copy of itself that fills the rest. Cached in the data folder.
    """
    w, h = cfg.get("render.width", 1080), cfg.get("render.height", 1920)
    W, H = int(w * margin), int(h * margin)
    cache = cfg.data_dir / "bg-cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{src.resolve()}:{src.stat().st_mtime_ns}:{W}x{H}:{min_fill}:v2".encode()).hexdigest()[:12]
    out = cache / f"{src.stem}-{key}.jpg"
    if out.exists():
        return out
    img = Image.open(src).convert("RGB")
    # Background: the same picture covering the frame, blurred.
    scale = max(W / img.width, H / img.height)
    cover = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    left, top = (cover.width - W) // 2, (cover.height - H) // 2
    canvas = cover.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(40))
    canvas = Image.blend(canvas, Image.new("RGB", (W, H), "black"), 0.35)
    # Foreground: the picture full width (or full height if it is very tall).
    # Wide pictures are enlarged to fill more of the frame, trimming at most a
    # third of their width, so they don't sit in a thin strip.
    fit = min(W / img.width, H * 0.92 / img.height)
    if img.height * fit < H * min_fill:
        fit = min(H * min_fill / img.height, W * 1.5 / img.width)
    fg = img.resize((int(img.width * fit), int(img.height * fit)), Image.LANCZOS)
    if fg.width > W:
        left = (fg.width - W) // 2
        fg = fg.crop((left, 0, left + W, fg.height))
    canvas.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    canvas.save(out, "JPEG", quality=92)
    return out


def build_layers(cfg: Config, recipe: Recipe, workdir: Path) -> list[tuple[Path, float, float, int, int]]:
    """Draw one PNG per segment. Returns (png, start, end, x, y) tuples."""
    w, h = cfg.get("render.width", 1080), cfg.get("render.height", 1920)
    theme = THEMES.get(recipe.theme, THEMES["ink"])
    serif_i, sans = cfg.font("serif_italic"), cfg.font("sans")
    layers: list[tuple[Path, float, float, int, int]] = []
    quote_bottom = h // 2
    for i, seg in enumerate(recipe.segments):
        if seg.role == "quote":
            img, quote_bottom = draw_text_layer((w, h), seg.text, serif_i, 72, theme["text"], center_y=h // 2 - 60)
        elif seg.role == "author":
            img, _ = draw_text_layer((w, h), seg.text, sans, 44, theme["accent"], top_y=quote_bottom + 56, max_height=200)
        elif seg.role == "header":
            img, _ = draw_text_layer((w, h), seg.text, sans, 56, theme["accent"], center_y=h // 2 - 40)
        else:  # hook, reflection, cta
            img, _ = draw_text_layer((w, h), seg.text, sans, 68, theme["text"], center_y=h // 2 - 40)
        path = workdir / f"seg{i}.png"
        x, y = _save_cropped(img, path)
        layers.append((path, seg.start, seg.end, x, y))

    handle = cfg.get("account.handle")
    if handle:
        img, _ = draw_text_layer((w, h), handle, sans, 34, theme["accent"], center_y=int(h * 0.88), max_height=80)
        path = workdir / "handle.png"
        x, y = _save_cropped(img, path)
        layers.append((path, 0.0, recipe.duration, x, y))
    return layers


def spoken_text(seg) -> str:
    """What the voice says for one on-screen line."""
    text = seg.text.strip().strip("“”\"").strip()
    if seg.role == "author":
        text = text.lstrip("—–- ").strip()
        # "Bhagavad Gita 2.47" -> "Bhagavad Gita, chapter 2, verse 47"
        text = re.sub(r"\s*\b(\d+)\.(\d+)-(\d+)\b", r", chapter \1, verses \2 to \3", text)
        text = re.sub(r"\s*\b(\d+)\.(\d+)\b", r", chapter \1, verse \2", text)
    return text


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


def speak(cfg: Config, text: str, out: Path) -> Path:
    """Turn text into speech with the macOS `say` command."""
    rate = str(cfg.get("render.voice_rate", 160))
    subprocess.run(["say", "-v", pick_voice(cfg), "-r", rate, "-o", str(out), text], check=True)
    return out


def voiceover_enabled(cfg: Config) -> bool:
    return bool(cfg.get("render.voiceover")) and shutil.which("say") is not None


def _voiceover(cfg: Config, recipe: Recipe, workdir: Path) -> tuple[Recipe, list[tuple[Path, float]]]:
    """Record each line and stretch the timing so every line stays on screen
    until it has been read. Returns the retimed recipe and (clip, start) pairs."""
    if not voiceover_enabled(cfg):
        return recipe, []
    lengths = []
    for n, seg in enumerate(recipe.segments):
        clip = speak(cfg, spoken_text(seg), workdir / f"voice-{n}.aiff")
        lengths.append((clip, _duration(clip)))
    return retime(recipe, lengths)


def retime(recipe: Recipe, clips: list[tuple[Path, float]], lead: float = 0.3, tail: float = 0.7
           ) -> tuple[Recipe, list[tuple[Path, float]]]:
    """Shift and stretch segments to fit their spoken length.

    Lines follow each other with the same gaps as before. The author line is
    spoken right after the quote, and both stay up until it has been read.
    """
    segs = [Segment(s.role, s.text, s.start, s.end) for s in recipe.segments]
    placed: list[tuple[Path, float]] = []
    shift = 0.0
    voice_end = 0.0
    quote = None
    for seg, (clip, length) in zip(segs, clips):
        seg.start += shift
        seg.end += shift
        if seg.role == "author" and quote is not None:
            v_start = max(seg.start, voice_end + 0.25)
        else:
            v_start = max(seg.start + lead, voice_end + 0.25)
        voice_end = v_start + length
        placed.append((clip, v_start))
        need = voice_end + tail
        if need > seg.end:
            extra = need - seg.end
            seg.end = need
            if seg.role == "author" and quote is not None:
                quote.end = max(quote.end, need)
            shift += extra
        if seg.role == "quote":
            quote = seg
    duration = round(max(recipe.duration + shift, voice_end + 0.8), 2)
    for seg in segs:
        if seg.end >= recipe.duration + shift - 1e-6 or seg.end > duration:
            seg.end = duration
    return dataclasses.replace(recipe, segments=segs, duration=duration), placed


def ffmpeg_command(cfg: Config, recipe: Recipe, layers, out: Path,
                   voice: list[tuple[Path, float]] | None = None) -> list[str]:
    w, h = cfg.get("render.width", 1080), cfg.get("render.height", 1920)
    fps = cfg.get("render.fps", 30)
    dur = recipe.duration
    theme = THEMES.get(recipe.theme, THEMES["ink"])
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    filters: list[str] = []

    backgrounds = _files(cfg.resolve("assets/backgrounds"), VIDEO_EXT | IMAGE_EXT)
    if backgrounds:
        bg = backgrounds[_stable_index(recipe.id, len(backgrounds))]
        if bg.suffix.lower() in VIDEO_EXT:
            args += ["-stream_loop", "-1", "-t", f"{dur}", "-i", str(bg)]
            filters.append(
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
                f"eq=brightness=-0.10:saturation=0.85,drawbox=color=black@0.35:t=fill,trim=duration={dur}[bg]"
            )
        else:
            framed = prepare_background(cfg, bg)
            args += ["-loop", "1", "-framerate", f"{fps}", "-t", f"{dur}", "-i", str(framed)]
            frames = int(dur * fps)
            dim = cfg.get("render.picture_dim", 0.25)
            # Slow push-in on the picture, darkened so the words stay readable.
            filters.append(
                f"[0:v]zoompan=z='1+0.08*on/{frames}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":s={w}x{h}:fps={fps},setsar=1,drawbox=color=black@{dim}:t=fill,trim=duration={dur}[bg]"
            )
    else:
        args += [
            "-f", "lavfi", "-i",
            # Drawn at half size and scaled up: the gradient is soft anyway.
            f"gradients=s={w // 2}x{h // 2}:c0={theme['c0']}:c1={theme['c1']}:x0=0:y0=0:x1={w // 2}:y1={h // 2}"
            f":speed=0.01:duration={dur}:rate={fps}",
        ]
        filters.append(f"[0:v]vignette=PI/5:eval=init,scale={w}:{h}:flags=bicubic,setsar=1[bg]")

    idx = 1
    prev = "bg"
    for n, (png, start, end, x, y) in enumerate(layers):
        args += ["-loop", "1", "-framerate", f"{fps}", "-t", f"{dur}", "-i", str(png)]
        fade_in = min(0.5, max(0.1, (end - start) / 4))
        fade_out_st = max(start, end - 0.4)
        chain = f"[{idx}:v]format=rgba,fade=t=in:st={start}:d={fade_in}:alpha=1"
        if end < dur - 0.05:
            chain += f",fade=t=out:st={fade_out_st}:d=0.4:alpha=1"
        filters.append(chain + f"[t{n}]")
        filters.append(f"[{prev}][t{n}]overlay={x}:{y}:enable='between(t,{start},{end})'[v{n}]")
        prev = f"v{n}"
        idx += 1
    filters.append(f"[{prev}]format=yuv420p[vout]")

    audio_labels = []
    music = _files(cfg.resolve("assets/music"), AUDIO_EXT)
    if music:
        track = music[_stable_index(recipe.id + "m", len(music))]
        # Start somewhere inside the track so reels sharing a track sound different.
        length = _duration(track)
        offset = _stable_index(recipe.id + "o", max(1, int(length - dur - 1))) if length > dur + 2 else 0
        args += ["-stream_loop", "-1", "-ss", f"{offset}", "-t", f"{dur}", "-i", str(track)]
        vol = cfg.get("render.music_volume", 0.35)
        if voice:
            vol = cfg.get("render.music_volume_with_voice", 0.15)
        filters.append(
            f"[{idx}:a]atrim=0:{dur},asetpts=N/SR/TB,volume={vol},afade=t=in:d=1,"
            f"afade=t=out:st={max(0, dur - 1.5)}:d=1.5,aresample=44100[am]"
        )
        audio_labels.append("[am]")
        idx += 1
    for k, (clip, start) in enumerate(voice or []):
        args += ["-i", str(clip)]
        ms = int(start * 1000)
        filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms},apad[av{k}]")
        audio_labels.append(f"[av{k}]")
        idx += 1
    if not audio_labels:
        args += ["-f", "lavfi", "-t", f"{dur}", "-i", "anullsrc=r=44100:cl=stereo"]
        filters.append(f"[{idx}:a]anull[aout]")
    elif len(audio_labels) == 1:
        filters.append(f"{audio_labels[0]}anull[aout]")
    else:
        filters.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=first:normalize=0[aout]")

    args += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{dur}", "-r", f"{fps}",
        "-c:v", "libx264", "-profile:v", "high", "-preset", cfg.get("render.preset", "medium"), "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]
    return args


def video_path(cfg: Config, recipe_id: str) -> Path:
    folder = cfg.data_dir / "videos"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{recipe_id}.mp4"


def render(cfg: Config, recipe: Recipe, force: bool = False) -> Path:
    out = video_path(cfg, recipe.id)
    if out.exists() and not force:
        return out
    if not shutil.which("ffmpeg"):
        raise RenderError("ffmpeg is not installed. On a Mac: brew install ffmpeg")
    with tempfile.TemporaryDirectory(prefix="philoreels-") as tmp:
        workdir = Path(tmp)
        recipe, voice = _voiceover(cfg, recipe, workdir)
        layers = build_layers(cfg, recipe, workdir)
        tmp_out = workdir / "out.mp4"
        cmd = ffmpeg_command(cfg, recipe, layers, tmp_out, voice)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RenderError(f"ffmpeg failed for reel {recipe.id}: {result.stderr.strip()[-2000:]}")
        shutil.move(str(tmp_out), out)
    quote = next((x for x in recipe.segments if x.role == "quote"), None)
    timing_path(out).write_text(json.dumps({"duration": recipe.duration,
                                            "quote_start": quote.start if quote else 0.0,
                                            "voiceover": bool(voice)}))
    return out


def timing_path(video: Path) -> Path:
    return video.with_suffix(".json")


def cover_time(video: Path, recipe: Recipe) -> float:
    """A moment in the video where the quote is fully on screen."""
    try:
        return json.loads(timing_path(video).read_text())["quote_start"] + 1.5
    except (OSError, ValueError, KeyError):
        quote = next((x for x in recipe.segments if x.role == "quote"), None)
        return quote.start + 1.5 if quote else 1.0


def cover_frame(cfg: Config, video: Path, at: float, out: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at}", "-i", str(video),
         "-frames:v", "1", str(out)],
        check=True,
    )
    return out
