"""What a plain install carries."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_a_plain_install_has_playwright():
    """Every feature in the plain install: the browser that draws slides needs no extra."""
    assert any(d.replace(" ", "").startswith("playwright>=") for d in _project()["dependencies"])


def test_the_video_extra_still_resolves():
    """Existing `showntell[video]` installs (MCP configs, the GitHub Action) keep working."""
    assert "video" in _project()["optional-dependencies"]
