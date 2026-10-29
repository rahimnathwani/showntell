"""Run inside the hosted container by scripts/container_check.sh: Chromium starts with its sandbox,
and one walkthrough goes through MCP from start to finish check to MP4 export. Exits 1 on failure."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from playwright.sync_api import sync_playwright


def fail(why: str) -> None:
    print(f"FAIL: {why}")
    sys.exit(1)


def sandbox() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(chromium_sandbox=True)
        page = browser.new_page()
        page.set_content("<p>sandboxed</p>")
        if page.inner_text("p") != "sandboxed":
            fail("Chromium drew nothing")
        browser.close()
    print("ok: Chromium starts with its sandbox")


def text(result) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


async def walkthrough() -> None:
    token = next(iter(json.loads(Path("/data/dev-logins.json").read_text()).values()))
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client("http://localhost:4747/mcp", http_client=http)) as c:
        r = await c.call_tool("start_walkthrough", {"title": "In a box", "opening": "# In a box\n\nThis runs in a container.\n"})
        if r.is_error:
            fail(f"start: {text(r)}")
        handle = r.structured_content["handle"]
        flow = "## Flow {#f}\n\n```mermaid\ngraph TB\n  A[Client] --> B[Server]\n```\n\nThe [client](#A) calls [the server](#B).\n"
        if (r := await c.call_tool("add_slides", {"handle": handle, "markdown": flow})).is_error:
            fail(f"add_slides: {text(r)}")
        r = await c.call_tool("finish_walkthrough", {"handle": handle})
        body = text(r)
        if r.is_error or "no_browser" in body or "could not run" in body:
            fail(f"finish did not draw the slides: {body[:400]}")
        if not Path(f"/data/walkthroughs/{handle.split('.')[0]}/poster.png").is_file():
            fail("finish drew no poster")
        print("ok: finish_walkthrough drew every slide")
        r = await c.call_tool("export_walkthrough", {"handle": handle})
        if r.is_error or not (r.structured_content or {}).get("bytes"):
            fail(f"export: {text(r)}")
        print(f"ok: MP4 export, {r.structured_content['bytes']} bytes")


if os.getuid() == 0:
    fail("running as root")
sandbox()
asyncio.run(walkthrough())
print("container check passed")
