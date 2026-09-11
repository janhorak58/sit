#!/usr/bin/env python3
"""Build the S-I-T tutorial master video, clips, captions, and thumbnail."""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import edge_tts
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
STORYBOARD = json.loads((ROOT / "storyboard.json").read_text(encoding="utf-8"))
SCENES = STORYBOARD["scenes"]
VOICE = STORYBOARD["voice"]
WIDTH, HEIGHT, FPS = 1920, 1080, 30
BG = "#0b090a"
PANEL = "#181315"
INK = "#fffafc"
MUTED = "#cfc3c8"
MAGENTA = "#e5007d"
PAPER = "#f9f6f4"
DARK_INK = "#171214"
FONT = Path("/usr/share/fonts/TTF/DejaVuSans.ttf")
FONT_BOLD = Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")
MONO = Path("/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf")

NARRATION_DIR = ROOT / "narration"
SLIDE_DIR = ROOT / "slides"
WORK_DIR = ROOT / "work"
SCENE_DIR = WORK_DIR / "scenes"
VISUAL_DIR = WORK_DIR / "visuals"
OUTPUT_DIR = ROOT / "output"
CLIP_DIR = OUTPUT_DIR / "clips"
for directory in (NARRATION_DIR, SLIDE_DIR, SCENE_DIR, VISUAL_DIR, OUTPUT_DIR, CLIP_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(MONO if mono else FONT_BOLD if bold else FONT), size)


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def wrapped(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], width_px: int, used_font: ImageFont.FreeTypeFont, fill: str, spacing: int = 12) -> int:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=used_font) <= width_px:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    x, y = xy
    line_height = used_font.size + spacing
    for line in lines:
        draw.text((x, y), line, font=used_font, fill=fill)
        y += line_height
    return y


def brand(draw: ImageDraw.ImageDraw, x: int = 88, y: int = 62, dark: bool = True) -> None:
    draw.ellipse((x, y + 10, x + 18, y + 28), fill=MAGENTA)
    draw.text((x + 34, y), "S·I·T", font=font(34, bold=True), fill=INK if dark else DARK_INK)
    draw.text((x + 176, y + 7), "SMART INTERACTIVE TRANSCRIBER", font=font(15, bold=True), fill=MUTED if dark else "#776a70")


def make_title(scene: dict, out: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    brand(draw)
    draw.rectangle((88, 188, 98, 806), fill=MAGENTA)
    draw.text((148, 230), "COMPLETE SETUP & USER GUIDE", font=font(23, bold=True), fill=MAGENTA)
    y = wrapped(draw, scene["title"], (148, 292), 1040, font(76, bold=True), INK, 15)
    draw.text((148, y + 34), f"Version {STORYBOARD['version']} · Arch Linux · English narration", font=font(25), fill=MUTED)
    bullets = scene.get("bullets") or ["Install", "Connect", "Record", "Transcribe", "Analyze"]
    bx = 1330
    for index, item in enumerate(bullets[:5]):
        by = 265 + index * 112
        rounded(draw, (bx, by, 1830, by + 78), 18, PANEL, "#33272c", 2)
        draw.ellipse((bx + 22, by + 27, bx + 42, by + 47), fill=MAGENTA)
        wrapped(draw, item, (bx + 62, by + 20), 420, font(21, bold=True), INK, 6)
    draw.text((148, 960), "Remote Spark workflow · Managed SSH tunnels · No local fallback in the demonstration", font=font(20), fill="#8f8187")
    image.save(out)


def make_slide(scene: dict, out: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    brand(draw, dark=False)
    draw.text((88, 155), f"CHAPTER {int(scene['id'])}", font=font(18, bold=True), fill=MAGENTA)
    y = wrapped(draw, scene["title"], (88, 202), 1120, font(58, bold=True), DARK_INK, 12)
    bullets = scene.get("bullets", [])
    code = scene.get("code")
    left_width = 900 if code else 1500
    y += 42
    for item in bullets:
        draw.ellipse((96, y + 11, 116, y + 31), fill=MAGENTA)
        y = wrapped(draw, item, (144, y), left_width - 80, font(27), DARK_INK, 10) + 24
    if code:
        x0, y0, x1, y1 = 1030, 180, 1832, 900
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        rounded(sd, (x0 + 12, y0 + 18, x1 + 12, y1 + 18), 24, "#00000044")
        shadow = shadow.filter(ImageFilter.GaussianBlur(15))
        image = Image.alpha_composite(image.convert("RGBA"), shadow).convert("RGB")
        draw = ImageDraw.Draw(image)
        rounded(draw, (x0, y0, x1, y1), 24, BG)
        draw.ellipse((x0 + 28, y0 + 26, x0 + 44, y0 + 42), fill="#ff5f57")
        draw.ellipse((x0 + 54, y0 + 26, x0 + 70, y0 + 42), fill="#febc2e")
        draw.ellipse((x0 + 80, y0 + 26, x0 + 96, y0 + 42), fill="#28c840")
        cy = y0 + 84
        for line in code.splitlines():
            parts = textwrap.wrap(line, width=56, subsequent_indent="  ") or [""]
            for part in parts:
                draw.text((x0 + 34, cy), part, font=font(20, mono=True), fill="#f8eaf1")
                cy += 34
            cy += 7
    draw.rectangle((88, 966, 1832, 970), fill="#ded5d9")
    draw.rectangle((88, 966, 88 + int(1744 * int(scene["id"]) / len(SCENES)), 970), fill=MAGENTA)
    draw.text((88, 994), "S·I·T COMPLETE GUIDE", font=font(16, bold=True), fill="#71656a")
    draw.text((1730, 994), scene["id"], font=font(16, bold=True), fill=MAGENTA)
    image.save(out)


def crop_or_fit(source: Path, crop_y: int | None = None) -> Image.Image:
    image = Image.open(source).convert("RGB")
    if crop_y is not None:
        if image.width != WIDTH:
            ratio = WIDTH / image.width
            image = image.resize((WIDTH, round(image.height * ratio)), Image.Resampling.LANCZOS)
        y = max(0, min(int(crop_y), max(0, image.height - HEIGHT)))
        return image.crop((0, y, WIDTH, y + HEIGHT))
    ratio = min(WIDTH / image.width, HEIGHT / image.height)
    resized = image.resize((round(image.width * ratio), round(image.height * ratio)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    x = (WIDTH - resized.width) // 2
    y = (HEIGHT - resized.height) // 2
    canvas.paste(resized, (x, y))
    return canvas


def make_visual(scene: dict, visual: dict, index: int) -> Path:
    out = VISUAL_DIR / f"{scene['id']}-{index:02d}.png"
    if visual["type"] == "title":
        make_title(scene, out)
    elif visual["type"] == "slide":
        make_slide(scene, out)
    else:
        crop_or_fit(ROOT / visual["path"], visual.get("crop_y")).save(out)
    return out


async def synthesize() -> None:
    for scene in SCENES:
        target = NARRATION_DIR / f"{scene['id']}.mp3"
        if target.exists() and target.stat().st_size > 1000:
            continue
        communicate = edge_tts.Communicate(scene["narration"], VOICE, rate="-6%", pitch="-2Hz", volume="+0%")
        await communicate.save(str(target))


def render_scene(scene: dict) -> Path:
    audio = NARRATION_DIR / f"{scene['id']}.mp3"
    audio_duration = duration(audio)
    visuals = [make_visual(scene, item, i) for i, item in enumerate(scene["visuals"], 1)]
    total_video = audio_duration + 0.8
    shares = [total_video / len(visuals)] * len(visuals)
    visual_clips: list[Path] = []
    for index, (image, seconds) in enumerate(zip(visuals, shares), 1):
        clip = WORK_DIR / f"visual-{scene['id']}-{index:02d}.mp4"
        fade_out = max(0.0, seconds - 0.35)
        run(
            "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(image),
            "-t", f"{seconds:.3f}", "-vf", f"scale={WIDTH}:{HEIGHT},fade=t=in:st=0:d=0.25,fade=t=out:st={fade_out:.3f}:d=0.35",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(clip),
        )
        visual_clips.append(clip)
    concat_file = WORK_DIR / f"visual-{scene['id']}.txt"
    concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in visual_clips), encoding="utf-8")
    silent = WORK_DIR / f"silent-{scene['id']}.mp4"
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent))
    target = SCENE_DIR / f"{scene['id']}.mp4"
    run(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=7,apad=pad_dur=0.8", "-shortest", "-movflags", "+faststart", str(target),
    )
    return target


def concat_videos(paths: list[Path], target: Path) -> None:
    manifest = WORK_DIR / f"concat-{target.stem}.txt"
    manifest.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(target))


def stamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def write_srt(scene_ids: list[str], target: Path) -> None:
    offset = 0.0
    cue = 1
    output: list[str] = []
    by_id = {scene["id"]: scene for scene in SCENES}
    for scene_id in scene_ids:
        scene = by_id[scene_id]
        scene_duration = duration(SCENE_DIR / f"{scene_id}.mp4")
        usable = max(0.5, scene_duration - 0.5)
        sentences = split_sentences(scene["narration"])
        weights = [max(1, len(sentence.split())) for sentence in sentences]
        total = sum(weights)
        cursor = offset
        for sentence, weight in zip(sentences, weights):
            span = usable * weight / total
            end = min(offset + usable, cursor + span)
            lines = textwrap.wrap(sentence, width=72)
            output.extend([str(cue), f"{stamp(cursor)} --> {stamp(end)}", "\n".join(lines), ""])
            cue += 1
            cursor = end
        offset += scene_duration
    target.write_text("\n".join(output), encoding="utf-8")


def clock(seconds: float) -> str:
    value = int(round(seconds))
    return f"{value // 60:02d}:{value % 60:02d}"


def make_thumbnail() -> None:
    canvas = Image.new("RGB", (1280, 720), BG)
    draw = ImageDraw.Draw(canvas)
    shot = crop_or_fit(ROOT / "assets/06-connections.png").resize((720, 405), Image.Resampling.LANCZOS)
    shot = shot.crop((120, 0, 720, 405))
    canvas.paste(shot, (650, 188))
    draw.rectangle((632, 168, 1260, 613), outline=MAGENTA, width=5)
    draw.text((66, 58), "S·I·T", font=font(42, bold=True), fill=MAGENTA)
    draw.text((66, 164), "COMPLETE", font=font(76, bold=True), fill=INK)
    draw.text((66, 246), "SETUP &", font=font(76, bold=True), fill=INK)
    draw.text((66, 328), "USER GUIDE", font=font(76, bold=True), fill=INK)
    rounded(draw, (66, 464, 536, 526), 14, MAGENTA)
    draw.text((92, 480), "ARCH LINUX · REMOTE SPARK", font=font(20, bold=True), fill=INK)
    draw.text((66, 614), "Install · Connect · Record · Transcribe · Analyze", font=font(22), fill=MUTED)
    canvas.save(OUTPUT_DIR / "thumbnail.png")


def main() -> None:
    asyncio.run(synthesize())
    narration_text = "\n\n".join(f"{scene['id']}. {scene['title']}\n{scene['narration']}" for scene in SCENES)
    (ROOT / "narration.txt").write_text(narration_text + "\n", encoding="utf-8")
    rendered = {scene["id"]: render_scene(scene) for scene in SCENES}

    master_ids = [scene["id"] for scene in SCENES]
    master = OUTPUT_DIR / "sit-complete-guide-0.1.0.mp4"
    concat_videos([rendered[scene_id] for scene_id in master_ids], master)
    write_srt(master_ids, OUTPUT_DIR / "sit-complete-guide-0.1.0.en.srt")

    clip_specs = {
        "01-install-and-launch": ["02", "03", "04", "05"],
        "02-connections": ["06", "07"],
        "03-recording": ["09", "10"],
        "04-upload-transcript-analysis": ["11", "12", "13"],
        "05-library-privacy-updates": ["08", "14", "15", "16"],
    }
    for name, ids in clip_specs.items():
        concat_videos([rendered[scene_id] for scene_id in ids], CLIP_DIR / f"{name}.mp4")
        write_srt(ids, CLIP_DIR / f"{name}.en.srt")

    offset = 0.0
    chapters: list[str] = []
    for scene in SCENES:
        chapters.append(f"{clock(offset)} {scene['title']}")
        offset += duration(rendered[scene["id"]])
    (OUTPUT_DIR / "youtube-chapters.txt").write_text("\n".join(chapters) + "\n", encoding="utf-8")
    make_thumbnail()
    print(json.dumps({
        "master": str(master),
        "duration_seconds": round(duration(master), 1),
        "clips": len(clip_specs),
        "voice": VOICE,
    }, indent=2))


if __name__ == "__main__":
    main()
