"""MP4 export: every frame captured by seeking the player, not by recording it in real time.

1. **Folder:** the walkthrough is exported to a temporary static folder (player, data.json,
   audio, captions.vtt) and served on loopback.
2. **Frames:** workers each open the player in capture mode and render a contiguous range of
   frames; each screenshot is piped straight into its own ffmpeg (image2pipe) as one H.264
   segment. A frame whose state key repeats reuses the previous screenshot.
3. **Sound:** the narration clips are placed at their timeline start times with numpy.
4. **Mux:** segments are joined without re-encoding, with AAC audio, the captions as a mov_text
   track and `+faststart`; the result's duration is checked against the timeline.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .capture import Capturer, CaptureUnavailable, launch, require_playwright, serve_folder
from .export import export, timeline, timeline_total
from .model import Walkthrough

TOLERANCE = 0.2  # seconds between the timeline and the file's duration
# Measured on a 263 s walkthrough, 4 workers: PNG frames and x264 medium 8.3x real time,
# veryfast 10.2x, JPEG frames 16x. Most frames repeat the last, so decoding and encoding them
# costs more than capturing; JPEG decodes fastest and x264 re-compresses to 4:2:0 regardless.
PRESET = "veryfast"
IMAGE = "jpeg"


def require_ffmpeg() -> None:
    missing = [x for x in ("ffmpeg", "ffprobe") if not shutil.which(x)]
    if missing:
        raise SystemExit(
            f"MP4 export needs {' and '.join(missing)} on PATH: install FFmpeg "
            "(macOS: `brew install ffmpeg`; Debian or Ubuntu: `apt install ffmpeg`)"
        )


def default_workers() -> int:
    return max(1, min(4, os.cpu_count() or 1))


# -- sound ----------------------------------------------------------------------------------


def soundtrack(folder: Path, total: float, dest: Path) -> int:
    """Write the narration of an exported folder as one WAV of exactly `total` seconds, each clip
    at its start on the player's timeline. Returns the number of clips placed."""
    import numpy as np
    import soundfile as sf

    data = json.loads((folder / "data.json").read_text())
    w = Walkthrough.from_state({**data, "key": "", "report_key": ""})
    audio_for = {x["id"]: x.get("audio") for s in data["slides"] for x in s["says"]}
    rate = 24000
    clips = []
    for start, _, say in timeline(w):
        rel = audio_for.get(say.id)
        if not rel or not (folder / rel).is_file():
            continue
        clip, sr = sf.read(folder / rel, dtype="float32", always_2d=False)
        if clip.ndim > 1:
            clip = clip.mean(axis=1)
        if clips and sr != rate:
            raise SystemExit(f"narration clips differ in sample rate ({sr} and {rate})")
        rate = sr
        clips.append((start, clip))
    track = np.zeros(max(1, round(total * rate)), dtype=np.float32)
    for start, clip in clips:
        i = round(start * rate)
        n = max(0, min(len(clip), len(track) - i))
        track[i : i + n] += clip[:n]
    sf.write(dest, np.clip(track, -1, 1), rate, subtype="PCM_16")
    return len(clips)


# -- frames ---------------------------------------------------------------------------------


def _segment_cmd(fps: int, out: Path, image: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-c:v",
        "mjpeg" if image == "jpeg" else "png",
        "-framerate",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        PRESET,
        "-crf",
        "20",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(fps * 10),
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        str(out),
    ]


async def _render_range(
    browser, url: str, fps: int, a: int, b: int, out: Path, image: str, captions: bool, motion: bool, progress: list[int]
) -> dict:
    """Frames a..b-1 into one segment file. Returns counts for the speed report."""
    proc = await asyncio.create_subprocess_exec(*_segment_cmd(fps, out, image), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    shots = 0
    try:
        async with Capturer(url, captions=captions, motion=motion, image=image, browser=browser) as c:
            last_key, last = None, b""
            for i in range(a, b):
                key = await c.seek(i / fps)
                if key != last_key:
                    last_key, last = key, await c.shot()
                    shots += 1
                proc.stdin.write(last)
                await proc.stdin.drain()
                progress[0] += 1
            errors = await c.errors()
            player_total = c.duration
    finally:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
        err = (await proc.stderr.read()).decode(errors="replace")
        code = await proc.wait()
    if code != 0:
        raise SystemExit(f"ffmpeg failed on frames {a}-{b - 1}: {err.strip()}")
    return {"frames": b - a, "shots": shots, "errors": errors, "total": player_total}


async def _render_frames(
    url: str, fps: int, n: int, workers: int, work: Path, image: str, captions: bool, motion: bool, quiet: bool
) -> tuple[list[Path], dict]:
    async_playwright = require_playwright()
    bounds = [round(n * k / workers) for k in range(workers + 1)]
    ranges = [(bounds[k], bounds[k + 1]) for k in range(workers) if bounds[k + 1] > bounds[k]]
    segs = [work / f"seg{k:03d}.mp4" for k in range(len(ranges))]
    progress = [0]
    async with async_playwright() as pw:
        # One browser per worker: screenshots of pages in one browser queue on its compositor.
        browsers = await asyncio.gather(*(launch(pw) for _ in ranges))
        try:
            tasks = [
                asyncio.create_task(_render_range(br, url, fps, a, b, seg, image, captions, motion, progress))
                for br, (a, b), seg in zip(browsers, ranges, segs, strict=True)
            ]
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, timeout=1.0, return_when=asyncio.FIRST_EXCEPTION)
                if not quiet:
                    print(f"\r  frames {progress[0]}/{n}", end="", file=sys.stderr, flush=True)
                failed = next((t for t in done if t.exception()), None)
                if failed:
                    for t in pending:
                        t.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise failed.exception()
            if not quiet:
                print(file=sys.stderr)
            results = [t.result() for t in tasks]
        finally:
            for br in browsers:
                await br.close()
    stats = {
        "frames": sum(r["frames"] for r in results),
        "shots": sum(r["shots"] for r in results),
        "errors": sorted({e for r in results for e in r["errors"]}),
        "total": results[0]["total"],
    }
    return segs, stats


# -- muxing and checks ------------------------------------------------------------------------


def _has_cues(vtt: Path) -> bool:
    return vtt.is_file() and "-->" in vtt.read_text()


def _mux(segs: list[Path], wav: Path, vtt: Path, out: Path, work: Path, title: str = "", description: str = "") -> None:
    listing = work / "segments.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segs))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(wav)]
    subs = _has_cues(vtt)
    if subs:
        cmd += ["-i", str(vtt)]
    cmd += ["-map", "0:v", "-map", "1:a"] + (["-map", "2:s"] if subs else [])
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"] + (["-c:s", "mov_text", "-metadata:s:s:0", "language=eng"] if subs else [])
    # Tags: the title (©nam) and description (desc) the file carries into players and chat apps, and
    # the encoder (©too), naming showntell and its version. libavformat always overwrites `encoder`
    # with its own name, but the MP4 muxer prefers `encoding_tool` for that atom.
    tags = ["-metadata", f"encoding_tool=showntell {__version__}"]
    if title:
        tags += ["-metadata", f"title={title}"]
    if description:
        tags += ["-metadata", f"description={description}"]
    cmd += [
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-flags:a",
        "+bitexact",
        *tags,
        "-movflags",
        "+faststart",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg could not join the video: {r.stderr.strip()}")


# Each step re-encodes the full-size file. Narration is most of a walkthrough's bytes (about
# 100 kb/s of AAC against 50 to 70 kb/s of mostly still picture), so audio goes first.
SHORT_720 = "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'"
FIT_STEPS = [
    ("64 kb/s audio", ["-c:v", "copy", "-b:a", "64k"]),
    (
        "720p picture and 48 kb/s audio",
        ["-vf", SHORT_720, "-c:v", "libx264", "-preset", PRESET, "-crf", "28", "-tune", "stillimage", "-pix_fmt", "yuv420p"]
        + ["-b:a", "48k"],
    ),
]


def fit_size(path: Path, max_bytes: int, work: Path) -> str:
    """Shrink an MP4 larger than `max_bytes`, for apps that cap attachments (WhatsApp sends
    videos up to 16 MB). Returns what was given up, or '' when it already fits."""
    size = path.stat().st_size
    if size <= max_bytes:
        return ""
    for what, args in FIT_STEPS:
        tmp = work / f"fit{path.suffix}"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-map", "0", "-c:a", "aac", "-c:s", "copy"] + args
        cmd += ["-map_metadata", "0", "-movflags", "+faststart", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"ffmpeg could not shrink the video: {r.stderr.strip()}")
        if tmp.stat().st_size <= max_bytes:
            shutil.move(tmp, path)
            return what
    raise SystemExit(
        f"the video is {size / 1e6:.1f} MB and still {tmp.stat().st_size / 1e6:.1f} MB with {what}, over the "
        f"{max_bytes / 1e6:.1f} MB limit; split the walkthrough in two or raise the limit"
    )


def probe(path: Path) -> dict:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:format_tags:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(r.stdout)


def check_duration(path: Path, expected: float) -> float:
    got = float(probe(path)["format"]["duration"])
    if abs(got - expected) > TOLERANCE:
        raise SystemExit(f"{path} lasts {got:.2f} s but the walkthrough lasts {expected:.2f} s; the video is out of step")
    return got


# -- entry point ----------------------------------------------------------------------------


def render_mp4(
    data_dir: Path,
    wid: str,
    out: Path,
    *,
    fps: int = 30,
    workers: int | None = None,
    captions: bool = False,
    motion: bool = False,
    image: str = IMAGE,
    quiet: bool = False,
    max_bytes: int = 0,
) -> dict:
    """Render walkthrough `wid` to an MP4 at `out`. Returns timing and frame counts. With
    `max_bytes`, a larger file is shrunk to fit (fit_size) or the export fails. `motion` keeps the
    idle motion (a drawing's breathing, a held close-up's creep), which makes every held frame new."""
    require_ffmpeg()
    try:
        require_playwright()
    except CaptureUnavailable as e:
        raise SystemExit(f"MP4 export: {e}") from e
    if fps < 1 or fps > 120:
        raise SystemExit("--fps must be between 1 and 120")
    workers = workers or default_workers()
    t0 = time.monotonic()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="showntell-mp4-") as tmp:
        work = Path(tmp)
        folder = export(data_dir, wid, work / "site")
        w = Walkthrough.from_state({**json.loads((folder / "data.json").read_text()), "key": "", "report_key": ""})
        total = timeline_total(w)
        n = max(1, round(total * fps))
        workers = max(1, min(workers, n))
        with serve_folder(folder) as url:
            try:
                segs, stats = asyncio.run(_render_frames(url, fps, n, workers, work, image, captions, motion, quiet))
            except CaptureUnavailable as e:
                raise SystemExit(f"MP4 export: {e}") from e
        if abs(stats["total"] - total) > 0.05:
            raise SystemExit(f"the player's timeline ({stats['total']:.2f} s) and the exporter's ({total:.2f} s) disagree")
        wav = work / "narration.wav"
        clips = soundtrack(folder, n / fps, wav)
        _mux(segs, wav, folder / "captions.vtt", out, work, w.title, w.description)
        try:
            shrunk = fit_size(out, max_bytes, work) if max_bytes else ""
        except SystemExit:
            out.unlink()  # too big to send is no use
            raise
    got = check_duration(out, total)
    wall = time.monotonic() - t0
    return {
        "duration": got,
        "timeline": total,
        "frames": n,
        "shots": stats["shots"],
        "clips": clips,
        "workers": workers,
        "seconds": wall,
        "speed": total / wall if wall else 0.0,
        "errors": stats["errors"],
        "fps": fps,
        "shrunk": shrunk,
    }
