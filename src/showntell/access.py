"""Who may do what with a walkthrough in hosted mode (docs/hosted-design.md, "Ownership and sharing").

The owner may do everything; someone it is shared with may watch it and report player findings;
anyone else is told it does not exist. Checked on every request, so a removed share takes effect
at once. These take a `Walkthrough` or its saved state dict, and assume hosted mode: local mode
has no owners and checks nothing.
"""

from __future__ import annotations

from .identity import User


def _get(w, name: str, default=None):
    return w.get(name, default) if isinstance(w, dict) else getattr(w, name, default)


def owns(w, user: User | None) -> bool:
    o = _get(w, "owner") or {}
    return user is not None and bool(o) and (o.get("iss"), o.get("sub")) == user.key


def shared_with(w, user: User | None) -> bool:
    if user is None:
        return False
    return bool(_get(w, "company")) or (bool(user.email) and user.email.lower() in (_get(w, "shares") or []))


def can_view(w, user: User | None) -> bool:
    return owns(w, user) or shared_with(w, user)


def role(w, user: User | None) -> str:
    """`yours`, `shared`, or empty when the user may not see it."""
    return "yours" if owns(w, user) else "shared" if shared_with(w, user) else ""
