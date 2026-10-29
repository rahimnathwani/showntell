"""What hosted sign-in keeps between requests and restarts: `auth.db` (SQLite) in the data folder.

Every secret a browser or MCP client holds (session ids, authorization codes, access and refresh
tokens) is stored only as its SHA-256, so the file is no use for impersonating anyone. Rows:

| Table | Holds |
|---|---|
| `users` | each signed-in user's key, address and the claims the sign-in rule reads again at refresh |
| `clients` | MCP clients made by dynamic registration |
| `pending` | sign-ins and authorization requests waiting for the provider or for consent (minutes) |
| `codes` | authorization codes (five minutes, one use) |
| `tokens` | access tokens (an hour) and refresh tokens (30 days, one use), in chains |
| `sessions` | browser sessions (12 hours) and their CSRF tokens |
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .identity import BrowserSession, BrowserSessions, User

SCHEMA = """
create table if not exists users (iss text, sub text, email text, claims text, seen real, primary key (iss, sub));
create table if not exists clients (client_id text primary key, info text, created real);
create table if not exists pending (id text primary key, kind text, data text, expires real);
create table if not exists codes (hash text primary key, data text, expires real, used integer default 0, chain text);
create table if not exists tokens (
  hash text primary key, kind text, chain text, client_id text, iss text, sub text, email text,
  resource text, scopes text, expires real, spent integer default 0
);
create index if not exists tokens_chain on tokens (chain);
create table if not exists sessions (hash text primary key, iss text, sub text, email text, csrf text, expires real);
"""


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class AuthDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("pragma journal_mode=wal")
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
        if new:
            os.chmod(path, 0o600)

    def run(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def change(self, sql: str, args: tuple = ()) -> int:
        """Run an update; the number of rows it changed."""
        with self._lock:
            return self._db.execute(sql, args).rowcount

    def sweep(self) -> None:
        now = time.time()
        for table in ("pending", "codes", "tokens", "sessions"):
            self.run(f"delete from {table} where expires < ?", (now - 86400,))  # a day's grace keeps reuse detectable

    # -- users ---------------------------------------------------------------------------------

    def remember(self, user: User, claims: dict) -> None:
        self.run(
            "insert or replace into users values (?, ?, ?, ?, ?)", (user.issuer, user.sub, user.email, json.dumps(claims), time.time())
        )

    def claims(self, iss: str, sub: str) -> dict | None:
        rows = self.run("select claims from users where iss = ? and sub = ?", (iss, sub))
        return json.loads(rows[0][0]) if rows else None

    # -- pending sign-ins and authorization requests ---------------------------------------------

    def hold(self, kind: str, data: dict, ttl: float, key: str | None = None) -> str:
        key = key or secrets.token_urlsafe(24)
        self.run("insert or replace into pending values (?, ?, ?, ?)", (key, kind, json.dumps(data), time.time() + ttl))
        return key

    def peek(self, kind: str, key: str) -> dict | None:
        rows = self.run("select data from pending where id = ? and kind = ? and expires > ?", (key or "", kind, time.time()))
        return json.loads(rows[0][0]) if rows else None

    def take(self, kind: str, key: str) -> dict | None:
        data = self.peek(kind, key)
        self.run("delete from pending where id = ?", (key or "",))
        return data


class DBSessions(BrowserSessions):
    """Browser sessions in `auth.db`, so a restart does not sign viewers out."""

    def __init__(self, db: AuthDB, ttl: float | None = None) -> None:
        super().__init__(**({"ttl": ttl} if ttl else {}))
        self.db = db

    def create(self, user: User, ttl: float | None = None) -> BrowserSession:
        s = BrowserSession(secrets.token_urlsafe(32), user, secrets.token_urlsafe(24), time.time() + (ttl or self.ttl))
        self.db.run("insert into sessions values (?, ?, ?, ?, ?, ?)", (digest(s.id), user.issuer, user.sub, user.email, s.csrf, s.expires))
        return s

    def get(self, sid: str) -> BrowserSession | None:
        rows = self.db.run(
            "select iss, sub, email, csrf, expires from sessions where hash = ? and expires > ?", (digest(sid or ""), time.time())
        )
        if not rows:
            return None
        iss, sub, email, csrf, expires = rows[0]
        return BrowserSession(sid, User(iss, sub, email), csrf, expires)

    def end(self, sid: str) -> None:
        self.db.run("delete from sessions where hash = ?", (digest(sid or ""),))

    def end_user(self, user: User) -> None:
        self.db.run("delete from sessions where iss = ? and sub = ?", (user.issuer, user.sub))
