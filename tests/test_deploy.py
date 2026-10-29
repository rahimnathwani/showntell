"""The container files keep the design's process rules (docs/hosted-design.md, "Process")."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_the_image_runs_unprivileged_with_a_pinned_uv():
    df = (ROOT / "Dockerfile").read_text()
    assert "USER 10001:10001" in df and "SHOWNTELL_MODE=hosted" in df and "SHOWNTELL_DATA=/data" in df
    assert "no-sandbox" not in df and "uv:latest" not in df


def test_compose_locks_the_container_down():
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    for rule in ("read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", "seccomp=chromium-seccomp.json", "showntell-data:/data"):
        assert rule in compose, rule
    assert "privileged" not in compose and "unconfined" not in compose


def test_the_seccomp_profile_is_dockers_default_plus_what_chromium_needs():
    profile = json.loads((ROOT / "deploy" / "chromium-seccomp.json").read_text())
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    added = [r for r in profile["syscalls"] if r.get("comment", "").startswith("showntell:")]
    assert len(added) == 1 and sorted(added[0]["names"]) == ["chroot", "clone", "setns", "unshare"]
    assert added[0]["action"] == "SCMP_ACT_ALLOW" and "args" not in added[0]


def test_the_env_example_names_every_sign_in_setting():
    env = (ROOT / "deploy" / ".env.example").read_text()
    for name in (
        "SHOWNTELL_DOMAIN",
        "SHOWNTELL_OIDC",
        "SHOWNTELL_OIDC_CLIENT_ID",
        "SHOWNTELL_OIDC_CLIENT_SECRET",
        "SHOWNTELL_ENTRA_TENANT",
        "SHOWNTELL_ALLOWED_DOMAINS",
    ):
        assert f"{name}=" in env, name
