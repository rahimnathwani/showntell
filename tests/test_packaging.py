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


def test_install_instructions_fetch_only_the_headless_shell():
    """`playwright install chromium` also fetches the full Chromium (about 195 MB) that headless
    capture never starts; docs, CI, the Action and the image fetch the headless shell alone."""
    import re

    files = ["README.md", "CONTRIBUTING.md", "action.yml", "Dockerfile", ".github/workflows/ci.yml", "src/showntell/doctor.py"]
    for f in files:
        text = (ROOT / f).read_text()
        for m in re.finditer(r"playwright install [^\n`']*chromium", text):
            assert "--only-shell" in m.group(0), f"{f}: {m.group(0)}"
        assert "showntell[video]" not in text, f  # the extra is only an alias now


def test_the_action_keeps_the_browser_off_for_a_page():
    assert 'FORMAT" == html ]] && export SHOWNTELL_BROWSER=off' in (ROOT / "action.yml").read_text()
