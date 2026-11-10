"""Model downloads (Hugging Face and the spaCy release) with progress, and offline use once cached."""

from __future__ import annotations

import io
import logging
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("showntell.assets")

SPACY_NAME = "en_core_web_sm"
SPACY_VERSION = "3.8.0"
SPACY_URL = (
    f"https://github.com/explosion/spacy-models/releases/download/"
    f"{SPACY_NAME}-{SPACY_VERSION}/{SPACY_NAME}-{SPACY_VERSION}-py3-none-any.whl"
)

Progress = Callable[[str, float], None]  # (what, fraction 0..1)


class Status:
    """What the speech side is doing, for /health, doctor and tool replies."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "starting"  # starting, downloading, loading, ready, error
        self.detail = ""
        self.fraction = 0.0

    def set(self, state: str, detail: str = "", fraction: float = 0.0) -> None:
        with self._lock:
            self.state, self.detail, self.fraction = state, detail, fraction

    def describe(self) -> str:
        if self.state == "downloading":
            return f"downloading {self.detail} ({self.fraction:.0%})"
        if self.state in ("loading", "starting"):
            return "loading the voice model"
        if self.state == "error":
            return f"speech failed to load: {self.detail}"
        return self.state


def hf_snapshot(repo: str, patterns: list[str], status: Status | None, label: str) -> Path:
    """Local path of a Hugging Face snapshot, downloading the matching files if needed."""
    from huggingface_hub import constants, snapshot_download

    try:
        path = Path(snapshot_download(repo, allow_patterns=patterns, local_files_only=True))
        if all(any(path.glob(pat)) for pat in patterns):  # complete, not just partly cached
            return path
    except Exception:
        pass
    if os.environ.get("HF_HUB_OFFLINE") == "1" or constants.HF_HUB_OFFLINE:
        raise RuntimeError(f"{repo} is not downloaded and HF_HUB_OFFLINE is set")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")  # downloads only, nothing else
    constants.HF_HUB_DISABLE_TELEMETRY = True
    log.info("Downloading %s from Hugging Face", repo)
    tqdm_class = _progress_tqdm(status, label) if status else None
    path = snapshot_download(repo, allow_patterns=patterns, tqdm_class=tqdm_class)
    return Path(path)


def go_offline() -> None:
    """Everything needed is cached: stop huggingface_hub from checking for updates."""
    from huggingface_hub import constants

    os.environ["HF_HUB_OFFLINE"] = "1"
    constants.HF_HUB_OFFLINE = True


def _progress_tqdm(status: Status, label: str):
    from tqdm.auto import tqdm

    class Tracked(tqdm):  # type: ignore[misc]
        def __init__(self, *a, **k):
            k.setdefault("file", sys.stderr)
            super().__init__(*a, **k)

        def update(self, n=1):
            out = super().update(n)
            if self.total and self.unit == "B":
                status.set("downloading", label, self.n / self.total)
            return out

    return Tracked


def spacy_dir(data_dir: Path) -> Path:
    return data_dir / "models" / f"{SPACY_NAME}-{SPACY_VERSION}"


def ensure_spacy(data_dir: Path, status: Status | None = None) -> Path | None:
    """Make misaki's spaCy model loadable. Returns the model folder, or None if installed as a package."""
    import spacy

    if spacy.util.is_package(SPACY_NAME):
        return None
    target = spacy_dir(data_dir)
    if (target / "config.cfg").is_file():
        _redirect_spacy(target)
        return target
    log.info("Downloading the spaCy English model (12 MB)")
    if status:
        status.set("downloading", "the spaCy English model", 0.0)
    req = urllib.request.Request(SPACY_URL, headers={"User-Agent": "showntell"})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        while chunk := r.read(1 << 16):
            buf.write(chunk)
            if status and total:
                status.set("downloading", "the spaCy English model", buf.tell() / total)
    prefix = f"{SPACY_NAME}/{SPACY_NAME}-{SPACY_VERSION}/"
    # A folder of its own: the daemon and `showntell doctor` may both fetch the model at once.
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=target.name + ".part-", dir=target.parent))
    try:
        with zipfile.ZipFile(buf) as z:
            for name in z.namelist():
                if name.startswith(prefix) and not name.endswith("/"):
                    dest = tmp / name[len(prefix) :]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(name))
        if not (tmp / "config.cfg").is_file():
            raise RuntimeError("the spaCy model archive had an unexpected layout")
        try:
            tmp.replace(target)
        except OSError:
            if not (target / "config.cfg").is_file():  # not another process finishing first
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _redirect_spacy(target)
    return target


def _redirect_spacy(path: Path) -> None:
    """misaki loads the model by package name; point that name at our folder."""
    import spacy

    if getattr(spacy, "_showntell_redirect", None) == str(path):
        return
    load, is_package = spacy.load, spacy.util.is_package

    def patched_load(name, *a, **k):
        return load(str(path) if name == SPACY_NAME else name, *a, **k)

    def patched_is_package(name):
        return True if name == SPACY_NAME else is_package(name)

    spacy.load = patched_load
    spacy.util.is_package = patched_is_package
    spacy._showntell_redirect = str(path)
