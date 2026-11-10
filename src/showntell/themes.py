"""Player themes: the names, the colour tokens a project may override, and checking those values."""

from __future__ import annotations

import json
import re
from pathlib import Path

THEMES = ("dark", "light", "paper", "contrast", "night", "ocean", "studio")
DEFAULT = "dark"
PROJECT_FILE = "showntell.theme.json"
CSS = Path(__file__).parent / "static" / "themes.css"

_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?\s*(,\s*[\d.]+\s*)?\)|transparent)$")


def token_names() -> list[str]:
    """Colour tokens defined by the default theme in themes.css."""
    css = CSS.read_text(encoding="utf-8")
    block = css[css.index(":root") : css.index("}", css.index(":root"))]
    return [n for n in re.findall(r"--([a-z0-9-]+):", block) if n != "scheme"]


def clean_tokens(tokens: dict) -> tuple[dict, list[str]]:
    """Keep known tokens with plain colour values; returns (tokens, problems)."""
    names = set(token_names())
    out, problems = {}, []
    for k, v in (tokens or {}).items():
        name = str(k).removeprefix("--")
        value = str(v).strip()
        if name not in names:
            problems.append(f"unknown token `{name}`")
        elif not _COLOR.match(value):
            problems.append(f"`{name}` must be a colour like #1a7f37 or rgba(0, 0, 0, 0.5)")
        else:
            out[name] = value
    return out, problems


def project_theme(root: str | None) -> tuple[str | None, dict, list[str]]:
    """The project's showntell.theme.json: {"theme": "light", "tokens": {"accent": "#0969da"}}."""
    if not root:
        return None, {}, []
    p = Path(root) / PROJECT_FILE
    if not p.is_file():
        return None, {}, []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, {}, [f"{PROJECT_FILE} is not valid JSON: {e}"]
    theme = d.get("theme")
    problems = []
    if theme is not None and theme not in THEMES:
        problems.append(f"{PROJECT_FILE}: theme `{theme}` is not one of {', '.join(THEMES)}")
        theme = None
    tokens, bad = clean_tokens(d.get("tokens", {}))
    return theme, tokens, [f"{PROJECT_FILE}: {b}" for b in bad] + problems


def style_attr(tokens: dict) -> str:
    """Inline CSS custom properties for already-cleaned tokens."""
    return "; ".join(f"--{k}: {v}" for k, v in sorted(tokens.items()))
