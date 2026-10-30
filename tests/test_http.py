"""HTTP routes behind the Guard: host and origin checks, reports, audio, player, data, health, RPC and tokens."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from mcp.server.transport_security import TransportSecuritySettings

from showntell.app import Guard
from showntell.config import Config
from showntell.server import add_routes, create_mcp

RPC_TOKEN = "rpc-token-for-tests"
BASE = "http://localhost:4799"
OPENING = "# How caching works\n\nEvery request checks a small cache first.\n"
JSON = {"Content-Type": "application/json"}


def make_app(service, cfg: Config):
    mcp = create_mcp(service)
    add_routes(mcp, service, RPC_TOKEN, time.time())
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )
    return Guard(mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=security, host=cfg.host), cfg)


@pytest.fixture
def local_cfg(tmp_path) -> Config:
    return Config(host="127.0.0.1", port=4799, data_dir=tmp_path / "data", public_url="", access_token="")


@pytest.fixture
def http(service, local_cfg):
    """`async with http() as c:` a client for the loopback daemon; pass `client=(ip, port)` to come from elsewhere."""

    def _client(client: tuple[str, int] = ("127.0.0.1", 50000), cfg: Config | None = None) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=make_app(service, cfg or local_cfg), client=client)
        return httpx.AsyncClient(transport=transport, base_url=BASE)

    return _client


@pytest.fixture
async def walk(service, store, voiced):
    """A voiced walkthrough: (session, handle)."""
    r = await service.start("Caching", "landscape", "", 1.0, None)
    await service.add(r["handle"], OPENING)
    s = store.by_handle(r["handle"])
    await voiced(s)
    return s


# -- host and origin --------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["localhost:4799", "127.0.0.1:4799", "[::1]:4799", "localhost"])
async def test_loopback_hosts_are_allowed(http, host):
    async with http() as c:
        assert (await c.get("/health", headers={"Host": host})).status_code == 200


@pytest.mark.parametrize("host", ["evil.example:4799", "localhost.evil.example", "192.168.1.5:4799", ""])
async def test_unknown_host_is_421(http, host):
    async with http() as c:
        r = await c.get("/health", headers={"Host": host})
    assert r.status_code == 421


async def test_public_url_host_is_allowed(http, tmp_path, service):
    cfg = Config(host="127.0.0.1", port=4799, data_dir=tmp_path, public_url="https://walk.example.com", access_token="")
    async with http(cfg=cfg) as c:
        assert (await c.get("/health", headers={"Host": "walk.example.com"})).status_code == 200


async def test_cross_origin_post_is_403(http, walk):
    async with http() as c:
        r = await c.post(
            f"/w/{walk.w.id}/report", json={"message": "x"}, headers={"Origin": "https://evil.example", "X-Report-Key": walk.w.report_key}
        )
    assert (r.status_code, r.text) == (403, "cross-origin request") and walk.player_issues == []


async def test_same_machine_origin_post_is_allowed(http, walk):
    async with http() as c:
        r = await c.post(
            f"/w/{walk.w.id}/report", json={"message": "x"}, headers={"Origin": "http://localhost:4799", "X-Report-Key": walk.w.report_key}
        )
    assert r.status_code == 200


async def test_non_json_post_is_415(http, walk):
    async with http() as c:
        r = await c.post(
            f"/w/{walk.w.id}/report",
            content=b"message=x",
            headers={"Content-Type": "application/x-www-form-urlencoded", "X-Report-Key": walk.w.report_key},
        )
    assert (r.status_code, r.text) == (415, "JSON only")


async def test_post_without_content_type_is_415(http, walk):
    async with http() as c:
        r = await c.post(f"/w/{walk.w.id}/delete", content=b"{}")
    assert r.status_code == 415 and (walk.store.walk_dir(walk.w.id)).is_dir()


# -- reports ----------------------------------------------------------------------------------------


async def test_report_without_key_is_403(http, walk):
    async with http() as c:
        r = await c.post(f"/w/{walk.w.id}/report", json={"message": "x"})
    assert r.status_code == 403 and walk.player_issues == []


async def test_report_with_wrong_key_is_403(http, walk):
    async with http() as c:
        r = await c.post(f"/w/{walk.w.id}/report", json={"message": "x"}, headers={"X-Report-Key": "wrong"})
    assert r.status_code == 403


async def test_report_with_right_key_reaches_the_agent(http, walk, service):
    async with http() as c:
        r = await c.post(f"/w/{walk.w.id}/report", json={"message": "Diagram failed"}, headers={"X-Report-Key": walk.w.report_key})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert walk.player_issues == ["Diagram failed"]


async def test_report_with_bad_json_is_400(http, walk):
    async with http() as c:
        r = await c.post(f"/w/{walk.w.id}/report", content=b"{nope", headers={**JSON, "X-Report-Key": walk.w.report_key})
    assert r.status_code == 400


async def test_report_for_unknown_walkthrough_is_404(http):
    async with http() as c:
        r = await c.post(f"/w/{'Z' * 22}/report", json={"message": "x"}, headers={"X-Report-Key": "k"})
    assert r.status_code == 404


# -- audio, assets, static ----------------------------------------------------------------------------


async def test_audio_of_the_walkthrough_is_served(http, walk):
    key = walk.w.slides[0].says[0].key
    async with http() as c:
        r = await c.get(f"/w/{walk.w.id}/audio/{key}.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert "immutable" in r.headers["cache-control"]


async def test_audio_of_another_walkthrough_is_404(http, walk, service, store, voiced):
    r = await service.start("Other", "landscape", "", 1.0, None)
    await service.add(r["handle"], "# Other\n\nA completely different sentence.\n")
    other = store.by_handle(r["handle"])
    await voiced(other)
    other_key = other.w.slides[0].says[0].key
    async with http() as c:
        assert (await c.get(f"/w/{walk.w.id}/audio/{other_key}.wav")).status_code == 404
        assert (await c.get(f"/w/{other.w.id}/audio/{other_key}.wav")).status_code == 200


async def test_audio_path_tricks_are_404(http, walk):
    async with http() as c:
        for name in ("..%2F..%2Fstate.json", ".wav", "nothing.wav"):
            assert (await c.get(f"/w/{walk.w.id}/audio/{name}")).status_code == 404


async def test_static_files_are_served_and_traversal_is_404(http):
    async with http() as c:
        assert (await c.get("/static/player.css")).status_code == 200
        assert (await c.get("/static/..%2F..%2Fserver.py")).status_code == 404


async def test_assets_of_unknown_walkthrough_are_404(http):
    async with http() as c:
        assert (await c.get(f"/w/{'Z' * 22}/assets/x.png")).status_code == 404


# -- player, data, index ------------------------------------------------------------------------------


async def test_player_link_redirects_to_trailing_slash(http, walk):
    async with http() as c:
        r = await c.get(f"/w/{walk.w.id}?t=abc")
    assert r.status_code == 308 and r.headers["location"] == f"/w/{walk.w.id}/?t=abc"


async def test_player_page_has_no_placeholders_left(http, walk):
    async with http() as c:
        r = await c.get(f"/w/{walk.w.id}/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "{{" not in r.text and "}}" not in r.text
    assert walk.w.report_key in r.text and "<title>Caching</title>" in r.text


async def test_player_page_escapes_the_title(http, service, store):
    r = await service.start("<script>alert(1)</script>", "landscape", "", 1.0, None)
    wid = r["handle"].split(".")[0]
    async with http() as c:
        page = (await c.get(f"/w/{wid}/")).text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


async def test_player_page_carries_the_description(http, service, store):
    r = await service.start("Caching", "landscape", "", 1.0, None, "", description='Reads "through" a cache')
    wid = r["handle"].split(".")[0]
    async with http() as c:
        page = (await c.get(f"/w/{wid}/")).text
    assert '<meta name="description" content="Reads &quot;through&quot; a cache">' in page
    assert '<meta property="og:title" content="Caching">' in page and "{{DESCRIPTION}}" not in page


async def test_poster_route_page_and_gallery(http, walk, store):
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
    async with http() as c:
        assert (await c.get(f"/w/{walk.w.id}/poster.png")).status_code == 404
        assert "og:image" not in (await c.get(f"/w/{walk.w.id}/")).text
        assert '<span class="thumb"></span>' in (await c.get("/")).text
        (store.walk_dir(walk.w.id) / "poster.png").write_bytes(png)
        r = await c.get(f"/w/{walk.w.id}/poster.png")
        assert r.status_code == 200 and r.content == png
        assert f'<meta property="og:image" content="{walk.url}/poster.png">' in (await c.get(f"/w/{walk.w.id}/")).text
        assert f'<img class="thumb" src="/w/{walk.w.id}/poster.png" alt="">' in (await c.get("/")).text


async def test_unknown_player_is_404(http):
    async with http() as c:
        assert (await c.get(f"/w/{'Z' * 22}/")).status_code == 404
        assert (await c.get(f"/w/{'Z' * 22}/data.json")).status_code == 404


async def test_data_json_shape(http, walk):
    async with http() as c:
        d = (await c.get(f"/w/{walk.w.id}/data.json")).json()
    assert set(d) == {"format", "id", "title", "description", "frame", "theme", "tokens", "status", "questions", "notes", "slides"}
    assert (d["format"], d["id"], d["title"], d["frame"], d["status"]) == (1, walk.w.id, "Caching", "landscape", "writing")
    (slide,) = d["slides"]
    assert set(slide) == {"id", "rev", "kind", "title", "classes", "shown", "visual", "says"}
    (say,) = slide["says"]
    assert say["status"] == "ready" and say["audio"] == f"audio/{walk.w.slides[0].says[0].key}.wav"
    assert say["duration"] > 0 and say["words"] and say["text"] == "Every request checks a small cache first."


async def test_data_json_hides_secrets(http, walk):
    async with http() as c:
        body = (await c.get(f"/w/{walk.w.id}/data.json")).text
    assert walk.w.key not in body and walk.w.report_key not in body


async def test_index_lists_walkthroughs(http, walk):
    async with http() as c:
        r = await c.get("/")
    assert r.status_code == 200 and f"/w/{walk.w.id}/" in r.text and "{{" not in r.text
    assert '<span class="desc">' not in r.text
    assert "voiced on this machine" in r.text and "showntell play file.md" in r.text
    walk.w.description = "A read-through <cache>"
    walk.save_now()
    async with http() as c:
        r = await c.get("/")
    assert '<span class="desc">A read-through &lt;cache&gt;</span>' in r.text


async def test_delete_route(http, walk, store):
    async with http() as c:
        assert (await c.post(f"/w/{walk.w.id}/delete", json={})).status_code == 200
        assert (await c.post(f"/w/{walk.w.id}/delete", json={})).status_code == 404
    assert store.get(walk.w.id) is None


# -- health ---------------------------------------------------------------------------------------------


async def test_health(http, walk):
    async with http() as c:
        h = (await c.get("/health")).json()
    assert h["app"] == "showntell" and h["version"]
    assert h["speech"]["backend"] == "fake" and h["speech"]["state"] == "ready"
    assert h["walkthroughs_open"] == 1 and h["busy"] is True


# -- RPC ------------------------------------------------------------------------------------------------


async def test_rpc_requires_the_bearer_token(http):
    async with http() as c:
        assert (await c.post("/api/rpc/list", json={})).status_code == 403
        assert (await c.post("/api/rpc/list", json={}, headers={"Authorization": "Bearer wrong"})).status_code == 403
        r = await c.post("/api/rpc/list", json={}, headers={"Authorization": f"Bearer {RPC_TOKEN}"})
    assert r.status_code == 200 and r.json() == {"result": []}


async def test_rpc_rejects_unknown_methods(http):
    async with http() as c:
        for method in ("delete", "__init__", "store"):
            r = await c.post(f"/api/rpc/{method}", json={}, headers={"Authorization": f"Bearer {RPC_TOKEN}"})
            assert r.status_code == 404


async def test_rpc_bad_arguments_are_400(http):
    auth = {"Authorization": f"Bearer {RPC_TOKEN}"}
    async with http() as c:
        assert (await c.post("/api/rpc/start", json={"nope": 1}, headers=auth)).status_code == 400
        assert (await c.post("/api/rpc/start", json=[1, 2], headers=auth)).status_code == 400
        assert (await c.post("/api/rpc/add", json={"handle": "x.y", "markdown": "a"}, headers=auth)).json()["error"]


async def test_rpc_runs_the_flow(http):
    auth = {"Authorization": f"Bearer {RPC_TOKEN}"}
    async with http() as c:
        start = (
            await c.post(
                "/api/rpc/start", json={"title": "T", "frame": "square", "voice": "", "speed": 1.0, "project_dir": None}, headers=auth
            )
        ).json()["result"]
        await c.post("/api/rpc/add", json={"handle": start["handle"], "markdown": OPENING}, headers=auth)
        p = (await c.post("/api/rpc/playable", json={"handle": start["handle"], "timeout": 5}, headers=auth)).json()
    assert p["result"]["status"] == "ready" and p["result"]["frame"] == "square"


async def test_rpc_from_another_machine_is_refused(http):
    async with http(client=("10.1.2.3", 5555)) as c:
        r = await c.post("/api/rpc/list", json={}, headers={"Authorization": f"Bearer {RPC_TOKEN}"})
    assert (r.status_code, r.text) == (403, "local clients only")


# -- access-token mode -----------------------------------------------------------------------------------


@pytest.fixture
def wide_cfg(tmp_path) -> Config:
    return Config(host="0.0.0.0", port=4799, data_dir=tmp_path / "data", public_url="", access_token="sekret-token")


async def test_token_mode_requires_the_token(http, wide_cfg, walk):
    async with http(client=("10.1.2.3", 5555), cfg=wide_cfg) as c:
        r = await c.get(f"/w/{walk.w.id}/", headers={"Host": "192.168.1.5:4799"})
    assert r.status_code == 401


async def test_token_mode_wrong_token_is_401(http, wide_cfg, walk):
    async with http(client=("10.1.2.3", 5555), cfg=wide_cfg) as c:
        assert (await c.get(f"/w/{walk.w.id}/?t=wrong")).status_code == 401


async def test_token_in_query_sets_a_cookie_that_works_later(http, wide_cfg, walk):
    async with http(client=("10.1.2.3", 5555), cfg=wide_cfg) as c:
        r = await c.get(f"/w/{walk.w.id}/?t=sekret-token", headers={"Host": "192.168.1.5:4799"})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"]
        assert "showntell_t=sekret-token" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
        again = await c.get(f"/w/{walk.w.id}/data.json", headers={"Host": "192.168.1.5:4799"})
    assert again.status_code == 200 and "set-cookie" not in again.headers


async def test_token_mode_health_needs_no_token(http, wide_cfg):
    async with http(client=("10.1.2.3", 5555), cfg=wide_cfg) as c:
        assert (await c.get("/health")).status_code == 200


async def test_token_mode_refuses_mcp_from_other_machines(http, wide_cfg):
    async with http(client=("10.1.2.3", 5555), cfg=wide_cfg) as c:
        r = await c.post(
            "/mcp?t=sekret-token",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert (r.status_code, r.text) == (403, "local clients only")


async def test_token_mode_accepts_same_origin_posts(http, wide_cfg, walk):
    async with http(client=("10.1.2.3", 5555), cfg=wide_cfg) as c:
        r = await c.post(
            f"/w/{walk.w.id}/report?t=sekret-token",
            json={"message": "m"},
            headers={"Host": "192.168.1.5:4799", "Origin": "http://192.168.1.5:4799", "X-Report-Key": walk.w.report_key},
        )
    assert r.status_code == 200


async def test_loopback_config_ignores_the_access_token(http, tmp_path, walk):
    cfg = Config(host="127.0.0.1", port=4799, data_dir=tmp_path, public_url="", access_token="ignored")
    async with http(cfg=cfg) as c:
        assert (await c.get(f"/w/{walk.w.id}/")).status_code == 200


async def test_deleting_ends_an_open_event_stream(service, local_cfg, walk, store, free_port):
    import uvicorn

    s = walk
    server = uvicorn.Server(uvicorn.Config(make_app(service, local_cfg), host="127.0.0.1", port=free_port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        async with httpx.AsyncClient(base_url=f"http://localhost:{free_port}", timeout=10) as c:
            async with c.stream("GET", f"/w/{s.w.id}/events") as stream:
                lines = stream.aiter_lines()
                assert (await anext(lines)).startswith("data: ")
                store.delete(s.w.id)

                async def drain() -> None:
                    async for _ in lines:
                        pass

                await asyncio.wait_for(drain(), 5)
        assert not s.subscribers and not store.walk_dir(s.w.id).exists()
    finally:
        server.should_exit = True
        await task
