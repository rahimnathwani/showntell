"""`showntell prune`: delete walkthroughs nobody has changed for a while, and the cached audio no
walkthrough uses any more. Nothing is deleted unless someone runs it."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

UNITS = {"h": 3600, "d": 86400, "w": 7 * 86400}


def parse_age(text: str) -> float:
    """`60d`, `8w`, `12h` or a bare number of days, in seconds."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hdw]?)\s*", text or "")
    if not m:
        raise ValueError(f"{text!r} is not an age such as 60d, 8w or 12h")
    return float(m.group(1)) * UNITS[m.group(2) or "d"]


def prune(data_dir: Path, older_than: float, dry_run: bool = False) -> dict:
    """Remove walkthroughs last changed (or, in hosted mode, viewed) before the cutoff, then audio files that no remaining
    walkthrough refers to and that are themselves older than the cutoff (audio made a moment ago
    may belong to a walkthrough whose state is not saved yet)."""
    cutoff = time.time() - older_than
    walks = data_dir / "walkthroughs"
    gone: list[dict] = []
    keep_keys: set[str] = set()
    for d in sorted(walks.iterdir()) if walks.is_dir() else []:
        st = d / "state.json"
        try:
            data = json.loads(st.read_text())
        except (OSError, ValueError):
            continue  # not a walkthrough, or one being written: leave it
        if max(data.get("updated", 0), data.get("viewed", 0)) < cutoff:  # `viewed`: hosted mode only
            gone.append({"id": d.name, "title": data.get("title", ""), "updated": data.get("updated", 0)})
            if not dry_run:
                shutil.rmtree(d)
        else:
            keep_keys.update(x["key"] for s in data.get("slides", []) for x in s.get("says", []) if x.get("key"))
    audio = data_dir / "audio"
    files, size = 0, 0
    for f in sorted(audio.iterdir()) if audio.is_dir() else []:
        key = f.name.split(".", 1)[0]
        if not f.is_file() or key in keep_keys or f.stat().st_mtime >= cutoff:
            continue
        files += 1
        size += f.stat().st_size
        if not dry_run:
            f.unlink()
    return {"walkthroughs": gone, "audio_files": files, "audio_bytes": size}
