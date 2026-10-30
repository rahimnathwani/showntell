"""MCP tools, prompts and resources through the SDK's in-process client."""

from __future__ import annotations

import json
import re

import pytest
from mcp.client import Client

from showntell import themes
from showntell.build import LIMITS
from showntell.server import create_mcp

OPENING = "# How caching works\n\nEvery request checks a small cache first.\n"
BODY = "## The lookup {#lookup}\n\n```python\ndef get(self, key):\n    return self._data[key]\n```\n\nIt [returns](#L2) the value.\n"


@pytest.fixture
def mcp(service) -> Client:
    """An unentered in-process client; tests enter it themselves so anyio's cancel scope opens and
    closes in the test's own task."""
    return Client(create_mcp(service))


async def start(client, **kw) -> dict:
    r = await client.call_tool("start_walkthrough", {"title": "Caching", **kw})
    assert not r.is_error, r.content
    return r.structured_content


def text(result) -> str:
    return "\n".join(c.text for c in result.content)


# -- listing ------------------------------------------------------------------------------------------


async def test_tools_are_listed_with_titles(mcp):
    async with mcp as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {
            "start_walkthrough",
            "add_slides",
            "revise_slide",
            "finish_walkthrough",
            "list_walkthroughs",
            "preview_slides",
            "export_walkthrough",
        }
        assert all(t.title and t.description for t in tools.values())


async def test_writing_tools_are_not_read_only_or_destructive(mcp):
    async with mcp as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        for name in ("start_walkthrough", "add_slides", "revise_slide", "finish_walkthrough", "export_walkthrough"):
            a = tools[name].annotations
            assert (a.read_only_hint, a.destructive_hint, a.open_world_hint) == (False, False, False), name


async def test_list_tool_is_read_only_and_closed_world(mcp):
    async with mcp as client:
        (t,) = [t for t in (await client.list_tools()).tools if t.name == "list_walkthroughs"]
        assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False


async def test_start_schema_offers_frames_and_voices(mcp):
    async with mcp as client:
        (t,) = [t for t in (await client.list_tools()).tools if t.name == "start_walkthrough"]
        props = t.input_schema["properties"]
        assert set(props) == {"title", "description", "frame", "voice", "speed", "project_dir", "opening", "theme"}
        assert set(props["theme"]["enum"]) == {"", *themes.THEMES}
        assert "af_heart" in props["voice"]["description"]
        assert t.input_schema["required"] == ["title"]


# -- the flow ------------------------------------------------------------------------------------------


async def test_start_returns_handle_and_url(mcp):
    async with mcp as client:
        r = await client.call_tool("start_walkthrough", {"title": "Caching"})
        data = r.structured_content
        assert data["status"] == "started" and data["issues"] == []
        assert data["handle"] in text(r) and data["url"] in text(r)


async def test_start_says_which_project_folder_files_are_read_from(mcp, tmp_path):
    async with mcp as client:
        r = await client.call_tool("start_walkthrough", {"title": "Caching", "project_dir": str(tmp_path)})
        assert str(tmp_path.resolve()) in text(r)
        r = await client.call_tool("start_walkthrough", {"title": "Caching", "project_dir": str(tmp_path), "opening": OPENING})
        assert str(tmp_path.resolve()) in text(r)


@pytest.mark.parametrize("mode", ["auto", "legacy"])  # roots in a round trip (2026-07-28), or asked mid-call
async def test_project_dir_must_be_inside_a_folder_the_client_shares(service, tmp_path, mode):
    from mcp_types import ListRootsResult, Root

    shared, other = tmp_path / "shared", tmp_path / "other"
    (shared / "sub").mkdir(parents=True)
    other.mkdir()

    async def roots(ctx):
        return ListRootsResult(roots=[Root(uri=shared.as_uri())])

    async with Client(create_mcp(service), list_roots_callback=roots, mode=mode) as client:
        (tool,) = [t for t in (await client.list_tools()).tools if t.name == "start_walkthrough"]
        assert "shared" not in tool.input_schema["properties"]
        r = await client.call_tool("start_walkthrough", {"title": "T", "project_dir": str(other)})
        assert r.is_error and "outside the folders this client shares" in text(r)
        r = await client.call_tool("start_walkthrough", {"title": "T", "project_dir": str(shared / "sub")})
        assert not r.is_error and str((shared / "sub").resolve()) in text(r)
        r = await client.call_tool("start_walkthrough", {"title": "T"})
        assert str(shared) in text(r)  # the first shared folder, when none is given


async def test_start_with_opening_returns_the_playable_link(mcp):
    """The opening can ride along with the start call, saving a model round trip."""
    async with mcp as client:
        r = await client.call_tool("start_walkthrough", {"title": "Caching", "opening": OPENING})
        data, t = r.structured_content, text(r)
        assert data["status"] == "playable" and data["handle"] in t and data["url"] in t
        assert "{{" not in t and len(t) > 1000  # the guide came with it
        assert "1/1 paragraphs voiced" in t  # counted after waiting for the opening, not before
        r2 = await client.call_tool("add_slides", {"handle": data["handle"], "markdown": "## Next\n\nMore words here.\n"})
        assert r2.structured_content["status"] == "added"


async def test_first_add_is_playable_with_url_and_filled_guide(mcp):
    async with mcp as client:
        h = await start(client, frame="portrait")
        r = await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING})
        t = text(r)
        assert r.structured_content["status"] == "playable" and r.structured_content["url"] == h["url"]
        assert h["url"] in t and "Playable" in t
        assert "# Writing a showntell walkthrough" in t
        assert "{{" not in t and "**portrait**" in t and f"{LIMITS['portrait']['lines']} lines" in t


async def test_guide_comes_only_with_the_first_playable_reply(mcp):
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING})
        r = await client.call_tool("add_slides", {"handle": h["handle"], "markdown": BODY})
        assert r.structured_content["status"] == "added"
        assert "# Writing a showntell walkthrough" not in text(r)


async def test_add_without_narration_asks_for_an_opening(mcp):
    async with mcp as client:
        h = await start(client)
        r = await client.call_tool("add_slides", {"handle": h["handle"], "markdown": "# Only a title\n"})
        assert r.structured_content["status"] == "waiting" and "Nothing to play yet" in text(r)


async def test_later_replies_list_issues(mcp):
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING})
        r = await client.call_tool("add_slides", {"handle": h["handle"], "markdown": BODY + "\nSee [far](#L40).\n"})
        data = r.structured_content
        assert set(data) >= {"url", "status", "issues"}
        assert data["issues"] and "points past the code" in data["issues"][0]["message"]
        assert data["issues"][0]["code"] == "cue_off_code" and data["issues"][0]["severity"] == "error"
        assert "Problems" in text(r) and "Slide ids: how-caching-works, lookup" in text(r)


async def test_player_reports_reach_the_agent_marked_untrusted(mcp, store):
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING})
        store.by_handle(h["handle"]).report("Mermaid parse error on line 3")
        r = await client.call_tool("add_slides", {"handle": h["handle"], "markdown": BODY})
        assert [d["message"] for d in r.structured_content["player_issues"]] == ["Mermaid parse error on line 3"]
        assert r.structured_content["player_issues"][0]["source"] == "player"
        assert "untrusted" in text(r)


async def test_revise_slide(mcp):
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING + "\n" + BODY})
        r = await client.call_tool("revise_slide", {"handle": h["handle"], "slide": "lookup", "markdown": "New words."})
        assert not r.is_error
        assert r.structured_content["status"] == "revised" and r.structured_content["slide"] == "lookup"
        assert "revision 2" in text(r)


async def test_finish_walkthrough(mcp):
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING})
        r = await client.call_tool("finish_walkthrough", {"handle": h["handle"], "questions": ["Why?"]})
        data = r.structured_content
        assert (data["status"], data["slides"], data["url"]) == ("finished", 1, h["url"])
        assert h["url"] in text(r) and "Finished" in text(r)
        assert data["alike"] == []


async def test_finish_with_findings_asks_for_fixes_before_telling_the_user(mcp, store):
    """A list of findings after "tell the user" gets skipped, so the reply leads with the fixes."""
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING + BODY})
        store.by_handle(h["handle"]).report({"code": "text_too_small", "slide": "lookup", "message": "Slide lookup: text is 9 px."})
        r = await client.call_tool("finish_walkthrough", {"handle": h["handle"]})
        t = text(r)
        assert h["url"] in t and "slides `lookup`" in t
        assert t.index("revise_slide") < t.index("tell the user") and "No need to finish again" in t


async def test_finish_names_alike_slides(mcp):
    async with mcp as client:
        h = await start(client)
        code = "```python\nx = 1\n```\n\nThe [line](#L1) sets it.\n\n"
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING + f"## A {{#a}}\n\n{code}## B {{#b}}\n\n{code}"})
        r = await client.call_tool("finish_walkthrough", {"handle": h["handle"]})
        assert "Slides `a`, `b` in a row look alike (code)" in text(r) and "revise_slide" in text(r)


async def test_list_walkthroughs(mcp):
    async with mcp as client:
        assert (await client.call_tool("list_walkthroughs", {})).structured_content["walkthroughs"] == []
        h = await start(client)
        r = await client.call_tool("list_walkthroughs", {})
        (row,) = r.structured_content["walkthroughs"]
        assert row["handle"] == h["handle"] and h["url"] in text(r)


async def test_description_shows_in_the_list(mcp):
    async with mcp as client:
        await client.call_tool("start_walkthrough", {"title": "Caching", "description": "A read-through cache, for backend developers."})
        r = await client.call_tool("list_walkthroughs", {})
        assert "- Caching: A read-through cache, for backend developers. — " in text(r)


# -- errors --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,args",
    [
        ("add_slides", {"markdown": "x"}),
        ("revise_slide", {"slide": "s", "markdown": "x"}),
        ("finish_walkthrough", {}),
    ],
)
async def test_bad_handles_are_tool_errors(mcp, tool, args):
    async with mcp as client:
        r = await client.call_tool(tool, {"handle": "nope.nope", **args})
        assert r.is_error and "unknown handle" in text(r)


async def test_bad_voice_is_a_tool_error(mcp):
    async with mcp as client:
        r = await client.call_tool("start_walkthrough", {"title": "T", "voice": "robot"})
        assert r.is_error and "unknown voice" in text(r)


async def test_bad_project_dir_is_a_tool_error(mcp, tmp_path):
    async with mcp as client:
        r = await client.call_tool("start_walkthrough", {"title": "T", "project_dir": str(tmp_path / "missing")})
        assert r.is_error and "not a folder" in text(r)


async def test_unknown_slide_is_a_tool_error(mcp):
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING})
        r = await client.call_tool("revise_slide", {"handle": h["handle"], "slide": "nope", "markdown": "x"})
        assert r.is_error and "no slide `nope`" in text(r)


# -- prompts and resources ------------------------------------------------------------------------------


async def test_prompts_exist_with_one_required_argument(mcp):
    async with mcp as client:
        prompts = {p.name: p for p in (await client.list_prompts()).prompts}
        assert set(prompts) >= {"diff", "review", "file", "topic"}
        assert [a.name for a in prompts["diff"].arguments] == ["target"]
        assert all(p.title and p.arguments[0].required for p in prompts.values())


@pytest.mark.parametrize(
    "name,arg,value",
    [
        ("diff", "target", "main..HEAD"),
        ("review", "target", "main...feature"),
        ("file", "path", "src/showntell/store.py"),
        ("topic", "topic", "consistent hashing"),
    ],
)
async def test_prompts_render_their_argument(mcp, name, arg, value):
    async with mcp as client:
        r = await client.get_prompt(name, {arg: value})
        body = r.messages[0].content.text
        assert value in body and "{" + arg + "}" not in body and "showntell" in body


async def test_walkthroughs_resource_reads(mcp):
    async with mcp as client:
        h = await start(client)
        resources = {str(r.uri): r for r in (await client.list_resources()).resources}
        assert "showntell://walkthroughs" in resources
        r = await client.read_resource("showntell://walkthroughs")
        rows = json.loads(r.contents[0].text)
        assert rows[0]["handle"] == h["handle"]


async def test_each_walkthrough_is_a_resource_with_its_markdown(mcp):
    async with mcp as client:
        h = await start(client)
        await client.call_tool("add_slides", {"handle": h["handle"], "markdown": OPENING})
        wid = h["handle"].split(".")[0]
        templates = (await client.list_resource_templates()).resource_templates
        assert templates
        uri = re.sub(r"\{[^}]+\}", wid, templates[0].uri_template)
        r = await client.read_resource(uri)
        assert "Every request checks a small cache first." in r.contents[0].text


async def test_structured_content_carries_the_whole_reply(mcp):
    """Claude Code shows the model only the structured content when there is any, so the guide
    and the instructions must be in it, not only in the text."""
    async with mcp as client:
        r = await client.call_tool("start_walkthrough", {"title": "Probe", "opening": "# Probe\n\nOne short sentence.\n"})
        msg = r.structured_content["message"]
        assert msg == text(r)
        assert "Writing a showntell walkthrough" in msg and r.structured_content["url"] in msg
        a = await client.call_tool("add_slides", {"handle": r.structured_content["handle"], "markdown": "## Next\n\nMore words.\n"})
        assert a.structured_content["message"] == text(a)


# -- export and file delivery ---------------------------------------------------------------------


async def _written(client, project) -> dict:
    h = await start(client, project_dir=str(project), opening=OPENING)
    await client.call_tool("add_slides", {"handle": h["handle"], "markdown": BODY})
    await client.call_tool("finish_walkthrough", {"handle": h["handle"]})
    return h


async def test_export_writes_one_page_in_the_project_folder(mcp, tmp_path):
    async with mcp as client:
        h = await _written(client, tmp_path)
        r = await client.call_tool("export_walkthrough", {"handle": h["handle"], "format": "html"})
        assert not r.is_error, text(r)
        data = r.structured_content
        out = tmp_path / "caching.html"
        assert data["path"] == str(out.resolve()) and data["bytes"] == out.stat().st_size and data["format"] == "html"
        assert str(out.resolve()) in text(r) and "<!doctype html>" in out.read_text().lower()[:200]
        r = await client.call_tool("export_walkthrough", {"handle": h["handle"], "format": "html", "path": "out/w.html"})
        assert r.is_error and "does not exist" in text(r)
        (tmp_path / "out").mkdir()
        r = await client.call_tool("export_walkthrough", {"handle": h["handle"], "format": "html", "path": "out/w.html"})
        assert not r.is_error and (tmp_path / "out" / "w.html").is_file()


async def test_export_checks_suffix_and_size(mcp, tmp_path):
    async with mcp as client:
        h = await _written(client, tmp_path)
        r = await client.call_tool("export_walkthrough", {"handle": h["handle"], "format": "html", "path": "w.mp4"})
        assert r.is_error and "must end in .html" in text(r)
        r = await client.call_tool("export_walkthrough", {"handle": h["handle"], "format": "html", "max_mb": 0.001})
        assert r.is_error and "over the 0.001 MB limit" in text(r)
        assert not (tmp_path / "caching.html").exists()
        r = await client.call_tool("export_walkthrough", {"handle": "nope.nope"})
        assert r.is_error and "unknown handle" in text(r)


async def test_export_path_must_be_inside_a_folder_the_client_shares(service, tmp_path):
    from mcp_types import ListRootsResult, Root

    shared, other = tmp_path / "shared", tmp_path / "other"
    shared.mkdir()
    other.mkdir()

    async def roots(ctx):
        return ListRootsResult(roots=[Root(uri=shared.as_uri())])

    async with Client(create_mcp(service), list_roots_callback=roots) as client:
        h = await _written(client, shared)
        r = await client.call_tool("export_walkthrough", {"handle": h["handle"], "format": "html", "path": str(other / "w.html")})
        assert r.is_error and "outside the folders this client shares" in text(r)
        r = await client.call_tool("export_walkthrough", {"handle": h["handle"], "format": "html", "path": str(shared / "w.html")})
        assert not r.is_error


def test_export_path_names_the_file_after_the_title(tmp_path):
    from showntell.service import export_path

    assert export_path("Why TLS? A tour!", str(tmp_path), "", "mp4") == tmp_path.resolve() / "why-tls-a-tour.mp4"
    assert export_path("???", str(tmp_path), "", "html") == tmp_path.resolve() / "walkthrough.html"
    assert export_path("T", None, str(tmp_path / "x.htm"), "html") == (tmp_path / "x.htm").resolve()
    for title, root, path, fmt, msg in [
        ("T", None, "", "mp4", "no project folder"),
        ("T", None, "x.mp4", "mp4", "relative path needs a project folder"),
        ("T", str(tmp_path), "x.html", "mp4", "must end in .mp4"),
        ("T", str(tmp_path), "nope/x.mp4", "mp4", "does not exist"),
    ]:
        with pytest.raises(ValueError, match=msg):
            export_path(title, root, path, fmt)
    (tmp_path / "d.mp4").mkdir()
    with pytest.raises(ValueError, match="is a folder"):
        export_path("T", str(tmp_path), "d.mp4", "mp4")


async def test_file_delivery_never_offers_the_link(service, tmp_path):
    """In file mode the user cannot open links on this machine: no reply hands the agent one."""
    async with Client(create_mcp(service, deliver="file")) as client:
        assert "export_walkthrough" in (client.instructions or "")
        h = await start(client, project_dir=str(tmp_path), opening=OPENING)
        url = h["url"]
        assert url not in text_of(h) and "{{" not in text_of(h) and "# Writing a showntell walkthrough" in text_of(h)
        r = await client.call_tool("add_slides", {"handle": h["handle"], "markdown": BODY})
        assert url not in text(r)
        r = await client.call_tool("revise_slide", {"handle": h["handle"], "slide": "lookup", "markdown": "New words."})
        assert url not in text(r)
        r = await client.call_tool("finish_walkthrough", {"handle": h["handle"]})
        assert url not in text(r) and "export_walkthrough" in text(r)


async def test_file_delivery_argument_text_reaches_the_schema(service):
    """The text/file/ wording for an argument replaces the local one in the tool's schema."""
    from showntell.server import texts

    want = texts("file")["tools"]["start_walkthrough"]["args"]["frame"]
    assert want != texts("link")["tools"]["start_walkthrough"]["args"]["frame"]
    async with Client(create_mcp(service, deliver="file")) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["start_walkthrough"].input_schema["properties"]["frame"]["description"] == want


async def test_link_delivery_guide_still_says_to_send_the_link(mcp):
    async with mcp as client:
        h = await start(client, opening=OPENING)
        assert "send it now" in text_of(h)


def text_of(structured: dict) -> str:
    return structured["message"]


def test_unknown_delivery_mode_is_refused(service):
    with pytest.raises(ValueError, match="deliver"):
        create_mcp(service, deliver="pigeon")


def test_file_texts_override_only_what_mentions_the_link():
    from showntell.server import texts

    base, file = texts("link"), texts("file")
    assert file["guide"] == base["guide"] and file["prompts"] == base["prompts"]
    assert set(file["tools"]) == set(base["tools"])
    for name, t in base["tools"].items():
        assert set(file["tools"][name]["args"]) == set(t.get("args", {})), name
    changed = {k for k in base["replies"] if file["replies"][k] != base["replies"][k]}
    assert changed >= {"playable", "finished", "guide_send_link", "guide_tell_user"}
    assert all("{url}" not in file["replies"][k] for k in changed)
