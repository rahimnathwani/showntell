"""Settings from the environment, with defaults. Command-line flags override these."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"SHOWNTELL_{name}", default)


def _num(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw) if raw.strip() else default
    except ValueError:
        raise SystemExit(f"SHOWNTELL_{name}={raw!r} is not a number") from None


@dataclass
class Limits:
    """Fair use in hosted mode (`SHOWNTELL_QUOTA_*`). Per user unless it says server-wide."""

    narration_min: float = field(default_factory=lambda: _num("QUOTA_NARRATION_MIN", 8))  # per walkthrough
    slides: int = field(default_factory=lambda: int(_num("QUOTA_SLIDES", 60)))  # per walkthrough
    writing: int = field(default_factory=lambda: int(_num("QUOTA_WRITING", 3)))  # walkthroughs in progress
    walkthroughs: int = field(default_factory=lambda: int(_num("QUOTA_WALKTHROUGHS", 200)))  # stored
    storage_gb: float = field(default_factory=lambda: _num("QUOTA_STORAGE_GB", 2))  # stored
    retention_days: float = field(default_factory=lambda: _num("QUOTA_RETENTION_DAYS", 180))  # after last view; 0 keeps
    calls_per_min: int = field(default_factory=lambda: int(_num("QUOTA_CALLS_PER_MIN", 60)))  # tool calls
    markdown_kb: float = field(default_factory=lambda: _num("QUOTA_MARKDOWN_KB", 64))  # per add_slides or revise_slide call
    browsers: int = field(default_factory=lambda: int(_num("QUOTA_BROWSERS", 2)))  # Chromium pages, server-wide
    check_s: float = field(default_factory=lambda: _num("QUOTA_CHECK_S", 60))  # per finish check or preview
    export_min: float = field(default_factory=lambda: _num("QUOTA_EXPORT_MIN", 15))  # per export


@dataclass
class Config:
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PORT", "4747")))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA", str(Path.home() / ".local/share/showntell"))).expanduser())
    voice: str = field(default_factory=lambda: _env("VOICE", "af_heart"))
    speech: str = field(default_factory=lambda: _env("SPEECH", "auto"))  # auto, mlx, onnx, fake
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL", ""))
    open_browser: bool = field(default_factory=lambda: _env("OPEN") == "1")
    access_token: str = field(default_factory=lambda: _env("ACCESS_TOKEN", ""))
    deliver: str = field(default_factory=lambda: _env("DELIVER", "link"))  # link, or file: the agent sends an export
    finish_check: bool = field(default_factory=lambda: _env("FINISH_CHECK", "1") != "0")  # layout check at finish
    mode: str = field(default_factory=lambda: _env("MODE", "local"))  # local, or hosted: docs/hosted-design.md
    dev_users: str = field(default_factory=lambda: _env("DEV_USERS", ""))  # hosted trials: emails for the stub login
    allowed_domains: str = field(default_factory=lambda: _env("ALLOWED_DOMAINS", ""))  # hosted: who may sign in and be shared with
    oidc: str = field(default_factory=lambda: _env("OIDC", ""))  # hosted sign-in: google, entra or generic
    oidc_client_id: str = field(default_factory=lambda: _env("OIDC_CLIENT_ID", ""))
    oidc_client_secret: str = field(default_factory=lambda: _env("OIDC_CLIENT_SECRET", ""))
    oidc_issuer: str = field(default_factory=lambda: _env("OIDC_ISSUER", ""))  # generic only
    entra_tenant: str = field(default_factory=lambda: _env("ENTRA_TENANT", ""))  # entra only: the tenant ID
    limits: Limits = field(default_factory=Limits)

    @property
    def hosted(self) -> bool:
        return self.mode == "hosted"

    @property
    def loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    @property
    def base_url(self) -> str:
        return (self.public_url or f"http://localhost:{self.port}").rstrip("/")

    @property
    def lockfile(self) -> Path:
        return self.data_dir / "daemon.json"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def lexicon_file(self) -> Path:
        return self.data_dir / "lexicon.txt"
