"""The shared daemon: lockfile, liveness, starting it with ensure(), and shutting it down over RPC."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from showntell import __version__, daemon
from showntell.config import Config
from showntell.service import RemoteService


@pytest.fixture
def cfg(tmp_path, free_port) -> Config:
    return Config(host="127.0.0.1", port=free_port, data_dir=tmp_path / "data", public_url="", access_token="")


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# -- lockfile ---------------------------------------------------------------------------------------


def test_write_and_read_lock(cfg):
    daemon.write_lock(cfg, "tok")
    lock = daemon.read_lock(cfg)
    assert lock["pid"] == os.getpid() and lock["port"] == cfg.port and lock["token"] == "tok"
    assert lock["version"] == __version__ and lock["url"] == f"http://localhost:{cfg.port}"


def test_lock_is_private(cfg):
    daemon.write_lock(cfg, "tok")
    assert stat.S_IMODE(cfg.lockfile.stat().st_mode) == 0o600
    assert not cfg.lockfile.with_suffix(".part").exists()


def test_clear_lock_removes_our_own_lock(cfg):
    daemon.write_lock(cfg, "tok")
    daemon.clear_lock(cfg)
    assert not cfg.lockfile.exists() and daemon.read_lock(cfg) is None


def test_clear_lock_leaves_another_process_lock(cfg):
    cfg.data_dir.mkdir(parents=True)
    cfg.lockfile.write_text(json.dumps({"pid": os.getpid() + 1, "port": cfg.port}))
    daemon.clear_lock(cfg)
    assert cfg.lockfile.exists()


def test_read_lock_tolerates_missing_and_corrupt_files(cfg):
    assert daemon.read_lock(cfg) is None
    cfg.data_dir.mkdir(parents=True)
    cfg.lockfile.write_text("{not json")
    assert daemon.read_lock(cfg) is None


# -- liveness ------------------------------------------------------------------------------------------


def test_running_is_none_without_a_lock(cfg):
    assert daemon.running(cfg) is None


def test_running_is_none_for_a_dead_pid(cfg):
    cfg.data_dir.mkdir(parents=True)
    cfg.lockfile.write_text(json.dumps({"pid": dead_pid(), "port": cfg.port, "token": "t"}))
    assert daemon.running(cfg) is None


def test_running_is_none_when_nothing_answers_health(cfg):
    daemon.write_lock(cfg, "tok")  # our own pid is alive, but nothing listens on the port
    assert daemon.running(cfg) is None


def test_health_is_none_when_nothing_listens(free_port):
    assert daemon.health(free_port, timeout=0.2) is None


@pytest.mark.skipif(os.name == "nt", reason="Windows has no TIME_WAIT reuse; SO_REUSEADDR there lets a port be stolen")
def test_a_port_a_stopped_daemon_left_in_time_wait_is_picked_again(free_port):
    import socket

    from showntell.app import pick_port

    # The side that closes first keeps the port in TIME_WAIT, as a daemon does when it stops.
    with socket.socket() as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", free_port))
        srv.listen()
        client = socket.create_connection(("127.0.0.1", free_port))
        conn, _ = srv.accept()
        conn.close()
    time.sleep(0.1)
    client.close()
    assert pick_port("127.0.0.1", free_port) == free_port


def test_ensure_passes_the_data_folder_to_the_child(cfg, monkeypatch):
    monkeypatch.delenv("SHOWNTELL_DATA", raising=False)
    seen = {}

    class FakePopen:
        def __init__(self, args, env=None, **kw):
            seen["args"], seen["env"] = args, env or {}

    calls = iter([None, {"pid": 1, "port": cfg.port, "token": "t", "version": __version__, "build": daemon.build_id()}])
    monkeypatch.setattr(daemon, "running", lambda c: next(calls))
    monkeypatch.setattr(daemon.subprocess, "Popen", FakePopen)
    daemon.ensure(cfg, log=lambda m: None)
    args = seen["args"]
    given = args[args.index("--data") + 1] if "--data" in args else seen["env"].get("SHOWNTELL_DATA")
    assert given == str(cfg.data_dir)


def test_ensure_reuses_a_running_daemon_of_this_version(cfg, monkeypatch):
    lock = {
        "pid": os.getpid(),
        "port": cfg.port,
        "token": "t",
        "version": __version__,
        "build": daemon.build_id(),
        "health": {"busy": False},
    }
    monkeypatch.setattr(daemon, "running", lambda c: lock)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start another daemon"))
    assert daemon.ensure(cfg, log=lambda m: None) is lock


@pytest.mark.parametrize("build", ["0000000000000000", None])
def test_ensure_replaces_an_idle_daemon_of_the_same_version_but_other_code(cfg, monkeypatch, build):
    """An update installed under the same version number (or a daemon from before build ids)."""
    old = {"pid": 999999, "port": cfg.port, "token": "t", "version": __version__, "health": {"busy": False}}
    if build:
        old["build"] = build
    new = {"pid": 1, "port": cfg.port, "token": "t2", "version": __version__, "build": daemon.build_id()}
    calls = iter([old, new])
    stopped, messages = [], []
    monkeypatch.setattr(daemon, "running", lambda c: next(calls))
    monkeypatch.setattr(daemon, "_shutdown", stopped.append)
    monkeypatch.setattr(daemon, "_alive", lambda pid: False)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: None)
    assert daemon.ensure(cfg, log=messages.append) is new
    assert stopped == [old] and "replacing" in messages[0]


@pytest.mark.parametrize("theirs", [{"version": "99.0.0"}, {"version": __version__, "built": 4e9}])
def test_ensure_keeps_a_daemon_newer_than_this_copy(cfg, monkeypatch, theirs):
    """Two installs (a plugin and a checkout) must not replace each other's daemon on every start."""
    lock = {"pid": os.getpid(), "port": cfg.port, "token": "t", "build": "0000000000000000", "health": {"busy": False}, **theirs}
    monkeypatch.setattr(daemon, "running", lambda c: lock)
    monkeypatch.setattr(daemon, "_shutdown", lambda lock: pytest.fail("must not stop a newer daemon"))
    messages = []
    assert daemon.ensure(cfg, log=messages.append) is lock
    assert "newer" in messages[0] and "showntell stop" in messages[0]


def test_newer_than_compares_versions_then_build_times():
    ours = daemon._version_key(__version__)
    older = ".".join(str(n) for n in (ours[0], ours[1], ours[2] - 1)) if ours[2] else "0.0.0"
    assert daemon.newer_than({"version": older, "built": 4e9})
    assert daemon.newer_than({"version": __version__, "built": 1.0})
    assert daemon.newer_than({"version": __version__})  # from before build times
    assert not daemon.newer_than({"version": __version__, "built": 4e9})


def test_build_id_changes_with_the_code(monkeypatch, tmp_path):
    import shutil

    pkg = tmp_path / "showntell"
    shutil.copytree(Path(daemon.__file__).parent, pkg, ignore=shutil.ignore_patterns("vendor", "__pycache__"))
    daemon.build_id.cache_clear()
    monkeypatch.setattr(daemon, "__file__", str(pkg / "daemon.py"))
    a = daemon.build_id()
    daemon.build_id.cache_clear()
    (pkg / "text" / "guide.md").write_text("changed")
    b = daemon.build_id()
    daemon.build_id.cache_clear()
    assert a != b and len(a) == 16


def test_ensure_keeps_a_busy_daemon_of_another_version(cfg, monkeypatch):
    lock = {"pid": os.getpid(), "port": cfg.port, "token": "t", "version": "0.0.1", "health": {"busy": True}}
    monkeypatch.setattr(daemon, "running", lambda c: lock)
    monkeypatch.setattr(daemon, "_shutdown", lambda lock: pytest.fail("must not stop a busy daemon"))
    messages = []
    assert daemon.ensure(cfg, log=messages.append) is lock
    assert "busy" in messages[0]


async def test_the_shim_reports_a_plain_text_refusal_readably():
    import httpx

    svc = RemoteService("http://127.0.0.1:1", "t")
    svc.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(403, text="local clients only", headers={"content-type": "text/plain"}))
    )
    with pytest.raises(ValueError, match="403: local clients only"):
        await svc.list()


def test_exporting_a_markdown_file_draws_no_slides_while_it_waits(cfg, monkeypatch, tmp_path):
    """Waiting for the audio polls finish; each poll must not launch a browser to check layout."""
    from showntell import export, service

    calls = []

    class Fake:
        def __init__(self, *a):
            self.n = 0

        async def start(self, *a, **k):
            return {"handle": "w.k"}

        async def add(self, *a):
            return {}

        async def playable(self, *a):
            return {}

        async def finish(self, handle, questions, check=True):
            calls.append(check)
            self.n += 1
            return {"pending": 3 - self.n}

    monkeypatch.setattr(service, "RemoteService", Fake)
    monkeypatch.setattr(daemon, "ensure", lambda c, log: {"port": 1, "token": "t"})
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    md = tmp_path / "talk.md"
    md.write_text("# Talk\n\nWords.\n")
    assert export._play_and_wait(cfg, md) == "w"
    assert calls == [False, False, False]


async def _no_sleep(s):
    return None


# -- the real thing -------------------------------------------------------------------------------------


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as r:
        return json.loads(r.read())


def _post(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url, data=b"{}", method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read())


def _reap(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if done:
            return True
        time.sleep(0.05)
    return False


async def test_the_shim_finds_a_replaced_daemon_again():
    # A daemon replaced since the shim started has a new token and refuses the old one.
    import httpx

    def answer(req):
        if req.headers["authorization"] == "Bearer new" and req.url.port == 2:
            return httpx.Response(200, json={"result": ["listed"]})
        return httpx.Response(403, json={"error": "forbidden"})

    found = []
    svc = RemoteService("http://127.0.0.1:1", "old", reconnect=lambda: found.append(1) or ("http://127.0.0.1:2", "new"))
    svc.client = httpx.AsyncClient(transport=httpx.MockTransport(answer), headers={"Authorization": "Bearer old"})
    assert await svc.list() == ["listed"] and found == [1]
    assert await svc.list() == ["listed"] and found == [1]  # found once, then kept


async def test_the_shim_finds_a_daemon_again_after_it_stopped():
    import httpx

    def answer(req):
        if req.url.port == 1:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(200, json={"result": []})

    svc = RemoteService("http://127.0.0.1:1", "t", reconnect=lambda: ("http://127.0.0.1:2", "t"))
    svc.client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    assert await svc.list() == []
    without = RemoteService("http://127.0.0.1:1", "t")
    without.client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    with pytest.raises(httpx.ConnectError):
        await without.list()


@pytest.fixture
def fake_daemon_env(cfg, monkeypatch):
    """Environment for a child daemon: fake speech, data in tmp, no browser, no token."""
    monkeypatch.setenv("SHOWNTELL_SPEECH", "fake")
    monkeypatch.setenv("SHOWNTELL_DATA", str(cfg.data_dir))
    monkeypatch.delenv("SHOWNTELL_OPEN", raising=False)
    monkeypatch.delenv("SHOWNTELL_ACCESS_TOKEN", raising=False)
    started: list[int] = []
    yield started
    for pid in started:  # never leave a daemon behind
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        _reap(pid, 1)


def _shut_down(cfg: Config, lock: dict) -> None:
    assert _post(f"http://127.0.0.1:{cfg.port}/api/rpc/shutdown", lock["token"]) == {"result": "bye"}
    assert _reap(int(lock["pid"]), 10), "the daemon did not exit"


@pytest.mark.slow
async def test_ensure_starts_a_daemon_that_serves_and_shuts_down(cfg, fake_daemon_env):
    lock = daemon.ensure(cfg, log=lambda m: None)
    pid = int(lock["pid"])
    fake_daemon_env.append(pid)
    assert lock["port"] == cfg.port and lock["version"] == __version__ and pid != os.getpid()
    assert stat.S_IMODE(cfg.lockfile.stat().st_mode) == 0o600
    health = _get_json(f"http://127.0.0.1:{cfg.port}/health")
    assert health["app"] == "showntell" and health["speech"]["backend"] == "fake"
    assert daemon.ensure(cfg, log=lambda m: None)["pid"] == pid  # a second ensure reuses it

    svc = RemoteService(f"http://127.0.0.1:{cfg.port}", lock["token"])
    try:
        start = await svc.start("Daemon test", "landscape", "", 1.0, None)
        await svc.add(start["handle"], "# Daemon test\n\nThe daemon voices this sentence.\n")
        assert (await svc.playable(start["handle"], 10))["status"] == "ready"
    finally:
        await svc.client.aclose()
    assert (cfg.data_dir / "walkthroughs" / start["handle"].split(".")[0] / "state.json").is_file()

    _shut_down(cfg, lock)
    assert daemon.health(cfg.port, 0.2) is None and daemon.running(cfg) is None


@pytest.mark.slow
async def test_a_shim_outlives_its_daemon(cfg, fake_daemon_env):
    def find() -> tuple[str, str]:
        lock = daemon.ensure(cfg, log=lambda m: None)
        fake_daemon_env.append(int(lock["pid"]))
        return f"http://127.0.0.1:{lock['port']}", lock["token"]

    first = daemon.ensure(cfg, log=lambda m: None)
    fake_daemon_env.append(int(first["pid"]))
    svc = RemoteService(f"http://127.0.0.1:{first['port']}", first["token"], reconnect=find)
    try:
        assert await svc.list() == []
        _shut_down(cfg, first)  # `showntell stop`, or another copy taking over
        start = await svc.start("After a restart", "landscape", "", 1.0, None)
        assert start["handle"] and daemon.read_lock(cfg)["pid"] != first["pid"]
    finally:
        await svc.client.aclose()


@pytest.mark.slow
def test_shutdown_clears_the_lockfile(cfg, fake_daemon_env):
    lock = daemon.ensure(cfg, log=lambda m: None)
    fake_daemon_env.append(int(lock["pid"]))
    _shut_down(cfg, lock)
    assert not cfg.lockfile.exists()


@pytest.mark.slow
def test_concurrent_clients_share_one_daemon(tmp_path):
    """Several MCP clients starting at once must not each start a daemon."""
    import concurrent.futures
    import os
    import socket
    import urllib.request

    from showntell import daemon
    from showntell.config import Config

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    old = os.environ.get("SHOWNTELL_SPEECH")
    os.environ["SHOWNTELL_SPEECH"] = "fake"
    try:

        def start(_):
            cfg = Config()
            cfg.data_dir, cfg.port = tmp_path, port
            return daemon.ensure(cfg, log=lambda m: None)["pid"]

        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            pids = set(ex.map(start, range(4)))
        assert len(pids) == 1
        lock = json.loads((tmp_path / "daemon.json").read_text())
        req = urllib.request.Request(
            f"http://127.0.0.1:{lock['port']}/api/rpc/shutdown",
            data=b"{}",
            method="POST",
            headers={"Authorization": f"Bearer {lock['token']}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3).read()
    finally:
        if old is None:
            os.environ.pop("SHOWNTELL_SPEECH", None)
        else:
            os.environ["SHOWNTELL_SPEECH"] = old


@pytest.mark.parametrize("ours", [True, False])
def test_ensure_replaces_a_daemon_without_the_browser_when_this_copy_has_it(cfg, monkeypatch, ours):
    """Adding the video extra must take effect: the same code with Playwright takes over from a
    daemon started without it, and a copy without it keeps the running daemon."""
    old = {
        "pid": 999999,
        "port": cfg.port,
        "token": "t",
        "version": __version__,
        "build": daemon.build_id(),
        "video": False,
        "health": {"busy": False},
    }
    new = {**old, "pid": 1, "token": "t2", "video": True}
    calls = iter([old, new])
    stopped = []
    monkeypatch.setattr(daemon, "has_video", lambda: ours)
    monkeypatch.setattr(daemon, "running", lambda c: next(calls))
    monkeypatch.setattr(daemon, "_shutdown", stopped.append)
    monkeypatch.setattr(daemon, "_alive", lambda pid: False)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: None)
    assert daemon.ensure(cfg, log=lambda m: None) is (new if ours else old)
    assert stopped == ([old] if ours else [])
