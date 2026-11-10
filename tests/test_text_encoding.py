"""Text files are read and written as UTF-8 whatever the locale. Left to the default, Python uses the
locale's encoding, cp1252 on most Windows machines, and the UTF-8 player page fails to load there."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "showntell"


def _const(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _unnamed(call: ast.Call) -> bool:
    """True when `call` does text I/O without saying which encoding."""
    if any(k.arg == "encoding" for k in call.keywords):
        return False
    f, n = call.func, len(call.args)
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    if name == "read_text":
        return n == 0  # Path.read_text(encoding, errors)
    if name == "write_text":
        return n < 2  # Path.write_text(data, encoding, ...)
    if (name == "open" and isinstance(f, ast.Name)) or name == "fdopen":  # open(file, mode, buffering, encoding)
        mode = _const(call.args[1]) if n > 1 else next((_const(k.value) for k in call.keywords if k.arg == "mode"), "r")
        return mode is not None and "b" not in mode and n < 4
    return False


def test_every_text_read_and_write_names_its_encoding():
    found = []
    for py in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _unnamed(node):
                found.append(f"{py.relative_to(SRC)}:{node.lineno}")
    assert not found, "pass encoding='utf-8' at: " + ", ".join(found)
