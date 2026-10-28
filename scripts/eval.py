"""Evaluate showntell end to end with real agents.

For each prompt and model, run Claude Code headless with showntell connected through the
stdio shim, watch its event stream, open the player in headless Chromium the moment the link
is playable, and play at 1x while the agent keeps writing. Afterwards, check cue timing and
pronunciation against speech recognition.

    uv run --with mlx-whisper python scripts/eval.py --models sonnet --runs tcp,csrf,arch,speedup,money,seasons
    uv run --with mlx-whisper python scripts/eval.py --models haiku --runs dns,tcp

Playwright comes from the project environment: `--with playwright` would resolve a newer release
whose browser build is not installed, and the player watch would fail on launch.
Results go to eval/results/<time>.json and a summary table is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import tempfile
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from showntell.checks import _layout, look  # noqa: E402
from showntell.model import Walkthrough  # noqa: E402

URL_RE = re.compile(r"http://(?:localhost|127\.0\.0\.1):\d+/w/[A-Za-z0-9_-]+/?")
RUN_TIMEOUT = 600  # seconds for one agent run; the longest so far took about 70

ALLOWED = [
    "mcp__showntell",
    "Read",
    "Grep",
    "Glob",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(ls:*)",
    "Bash(uv run pytest --collect-only:*)",
    "Bash(uv run showntell --help:*)",
]


async def run_agent(prompt: dict, model: str, data_dir: Path, port: int, play: bool) -> dict:
    cfg = {
        "mcpServers": {
            "showntell": {
                "command": "uv",
                "args": ["run", "--quiet", "--directory", str(ROOT), "showntell", "mcp"],
                "env": {"SHOWNTELL_DATA": str(data_dir), "SHOWNTELL_PORT": str(port)},
            }
        }
    }
    cfg_file = data_dir / f"mcp-{prompt['id']}-{model}.json"
    cfg_file.write_text(json.dumps(cfg))
    cmd = [
        "claude",
        "-p",
        prompt["prompt"],
        "--model",
        model,
        "--mcp-config",
        str(cfg_file),
        "--strict-mcp-config",
        "--output-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        *ALLOWED,
    ]
    t0 = time.time()
    # stderr goes to a file: an unread pipe would block the agent once it filled.
    err_file = (data_dir / f"claude-{prompt['id']}-{model}.stderr").open("wb")
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=err_file, stdin=asyncio.subprocess.DEVNULL, limit=1 << 24
    )
    marks: dict = {"tool_calls": [], "issues": 0, "player_issues": 0, "issue_codes": {}}
    player_task = None
    url = None
    tool_names: dict[str, str] = {}
    tool_inputs: dict[str, dict] = {}
    try:
        # One hung agent must not hold the whole batch: kill it and record the timeout.
        async with asyncio.timeout(RUN_TIMEOUT):
            async for raw in proc.stdout:
                now = time.time() - t0
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "assistant":
                    for block in ev["message"].get("content", []):
                        if block.get("type") == "tool_use":
                            name = block["name"].split("__")[-1]
                            tool_names[block["id"]] = name
                            tool_inputs[block["id"]] = block.get("input", {})
                            marks["tool_calls"].append((round(now, 2), name))
                            if name == "start_walkthrough":
                                marks.setdefault("start_call", now)
                        elif block.get("type") == "text" and URL_RE.search(block.get("text", "")) and "link_shared" not in marks:
                            marks["link_shared"] = now
                elif ev.get("type") == "user":
                    for block in ev["message"].get("content", []):
                        if block.get("type") != "tool_result":
                            continue
                        name = tool_names.get(block.get("tool_use_id"), "")
                        text = (
                            block["content"]
                            if isinstance(block["content"], str)
                            else " ".join(c.get("text", "") for c in block["content"] if isinstance(c, dict))
                        )
                        # Claude Code passes the structured content (JSON) when a tool returns some.
                        try:
                            data = json.loads(text)
                        except ValueError:
                            data = None
                        if isinstance(data, dict):
                            text = data.get("message", text)
                            for d in data.get("issues", []) + data.get("player_issues", []):
                                if isinstance(d, dict):
                                    marks["issue_codes"][d.get("code", "other")] = marks["issue_codes"].get(d.get("code", "other"), 0) + 1
                            marks["issues"] += len(data.get("issues", []))
                            marks["player_issues"] += len(data.get("player_issues", []))
                        else:
                            marks["issues"] += (
                                len(re.findall(r"^- ", text.split("The open player reported")[0], re.M)) if "Problems" in text else 0
                            )
                            for code in re.findall(r"^- .*\((\w+)\)$", text, re.M):
                                marks["issue_codes"][code] = marks["issue_codes"].get(code, 0) + 1
                            if "The open player reported" in text:
                                marks["player_issues"] += text.split("The open player reported")[1].count("\n- ")
                        if (
                            name in ("start_walkthrough", "add_slides")
                            and "playable_at" not in marks
                            and re.search(r"playable", text, re.I)
                        ):
                            m = URL_RE.search(text)
                            if m:
                                marks["playable_at"] = now
                                url = m.group(0).rstrip("/") + "/"
                                if play:
                                    player_task = asyncio.create_task(play_live(url))
                        if name == "finish_walkthrough":
                            marks["finished_at"] = now
                        if name == "start_walkthrough":
                            marks["opening_in_start"] = "opening" in str(tool_inputs.get(block.get("tool_use_id"), {}))
                elif ev.get("type") == "result":
                    marks["cost"] = ev.get("total_cost_usd")
                    marks["turns"] = ev.get("num_turns")
                    marks["result_text"] = (ev.get("result") or "")[-400:]
            await proc.wait()
    except TimeoutError:
        marks["timed_out"] = True
        proc.kill()
        await proc.wait()
    err_file.close()
    marks["exit"] = proc.returncode
    marks["wall"] = round(time.time() - t0, 1)
    marks["url"] = url
    if player_task:
        marks["player"] = await player_task
    if "start_call" in marks and "playable_at" in marks:
        marks["link_after_start"] = round(marks["playable_at"] - marks["start_call"], 2)
    return marks


async def play_live(url: str) -> dict:
    """Play from the start at 1x in headless Chromium, as a viewer who clicked the link at once.
    A browser that fails to launch is recorded as an error rather than raised into the batch."""
    try:
        return await _play_live(url)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}".strip()[:300]}


async def _play_live(url: str) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        page = await b.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(url + "?autoplay=1")
        t0 = time.time()
        while time.time() - t0 < 900:
            await page.wait_for_timeout(1000)
            m = await page.evaluate("window.__showntell")
            if m and m.get("ended"):
                break
        m = await page.evaluate("window.__showntell") or {}
        await b.close()
        return {
            "stalls": m.get("stalls", 0),
            "stall_s": round(m.get("stallSeconds", 0), 1),
            "render_errors": m.get("renderErrors", []) + errors,
            "ended": m.get("ended", False),
            "findings": m.get("findings", []),
            "start_delay_s": m.get("startDelay"),
            "holds": m.get("holds", 0),
            "watch_s": round(time.time() - t0, 1),
        }


async def check_browser() -> None:
    """Stop before spending on agents when headless Chromium is missing: without it the daemon's
    finish-time layout check and the player watch both fail, and the table says little."""
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            await b.close()
    except Exception as e:  # noqa: BLE001
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        sys.exit(f"headless Chromium does not launch ({first}); run `uv run playwright install chromium` first")


def walkthrough_for(data_dir: Path, url: str | None) -> Walkthrough | None:
    if not url:
        return None
    wid = url.rstrip("/").rsplit("/", 1)[-1]
    st = data_dir / "walkthroughs" / wid / "state.json"
    return Walkthrough.from_state(json.loads(st.read_text())) if st.is_file() else None


def norm_words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def spoken_words(display: str, code: list) -> list[str]:
    """Words the voice says for a piece of display text: identifiers split, symbols named,
    inline phonemes reduced to their word."""
    from showntell.pronounce import speakable

    text = speakable(display, code, [], {}).text
    text = re.sub(r"\[([^\]]*)\]\(/[^)]*/\)", r"\1", text)
    return norm_words(text)


SYMBOL_WORDS = {"dot", "slash", "dash", "underscore", "equals", "colon", "point", "hash", "plus", "minus", "times"}


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def heard_as(word: str, token: str) -> bool:
    """A transcript token matches a spoken word, allowing plurals, hyphen splits, numbers
    written as digits, and small spelling differences (normalises / normalizes)."""
    if word == token:
        return True
    if token.isdigit():
        from num2words import num2words

        token = re.sub(r"[^a-z]", "", num2words(int(token)).split()[0].split("-")[0])
        if word == token or word.startswith(token):
            return True
    short, long_ = sorted((word, token), key=len)
    if len(short) >= 3 and long_.startswith(short):
        return True
    return len(word) >= 5 and len(token) >= 4 and _distance(word, token) <= 2


def asr_checks(w: Walkthrough, audio_dir: Path) -> dict:
    """Cue timing against whisper word times, and words missing from the transcript."""
    import mlx_whisper

    cue_total = cue_ok = unlocated = 0
    deltas: list[float] = []
    missing_terms: list[str] = []
    audio_s = 0.0
    for slide in w.slides:
        for say in slide.says:
            if not say.text or say.status != "ready":
                continue
            f = audio_dir / f"{say.key}.wav"
            if not f.is_file():
                continue
            r = mlx_whisper.transcribe(
                str(f),
                path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                word_timestamps=True,
                language="en",
                condition_on_previous_text=False,
            )
            words = [(norm_words(x["word"]), x["start"]) for seg in r["segments"] for x in seg.get("words", [])]
            flat = [(t, s) for ws, s in words for t in ws]
            heard = {t for t, _ in flat}
            audio_s += say.duration

            # terms written in backticks must be audible. Whisper writes some spoken forms back
            # as text ("server dot pie" as server.py, numbers as digits), so a term counts as
            # heard when either its spoken words or its written words all come back.
            def all_heard(words: list[str], heard: set = heard) -> bool:
                words = [t for t in words if len(t) > 2 and t not in SYMBOL_WORDS]
                return all(any(heard_as(t, h) for h in heard) for t in words)

            for a, b in say.code:
                term = say.text[a:b]
                if not (all_heard(spoken_words(term, [(0, b - a)])) or all_heard(norm_words(term))):
                    missing_terms.append(term)
            for c in say.cues:
                if c.get("inferred") or "t" not in c:
                    continue
                cue_total += 1
                a, b = c["at"], c["end"]
                code_in = [(max(a, x) - a, min(b, y) - a) for x, y in say.code if x < b and y > a]
                label = spoken_words(say.text[a:b], code_in)
                if not label:
                    continue
                # candidate whisper times for the label's first spoken word, nearest the expected time
                cands = [s for t, s in flat if heard_as(label[0], t)]
                if not cands:
                    unlocated += 1  # the transcript spells it differently: timing cannot be checked
                    continue
                best = min(cands, key=lambda s: abs(s - c["t"]))
                d = abs(best - c["t"])
                deltas.append(d)
                if d <= 0.3:
                    cue_ok += 1
    minutes = max(audio_s / 60, 1e-6)
    located = cue_total - unlocated
    return {
        "cues": cue_total,
        "cues_ok": cue_ok,
        "cues_located": located,
        "cue_accuracy": round(cue_ok / located, 3) if located else None,
        "cue_mean_delta": round(sum(deltas) / len(deltas), 3) if deltas else None,
        "missing_terms": missing_terms[:20],
        "missing_per_min": round(len(missing_terms) / minutes, 2),
        "audio_min": round(minutes, 2),
    }


PLAIN = ("list", "table", "text")  # visuals made only of words


def summarize(w: Walkthrough | None, expect: list[str] | None = None) -> dict:
    """What the walkthrough holds. `plain_share` is the share of content slides (not the title or
    chapter cards) that are only lists, tables or text; `fit` is whether any of the visuals the
    prompt calls for (`expect` in prompts.json) appears, or None when the prompt names none."""
    if not w:
        return {}
    says = [x for s in w.slides for x in s.says if x.text]
    cues = [c for x in says for c in x.cues]
    used = set()
    for s in w.slides:
        v = s.visual or {}
        used |= {
            k
            for k, on in {
                "steps": bool(v.get("steps")),
                "scroll": bool(v.get("scroll")),
                "frame": bool(v.get("frame")),
                "notes": bool(v.get("notes")),
                "stack": v.get("style") == "stack",
                "checklist": v.get("style") == "checklist",
                "quiz": v.get("style") == "quiz",
            }.items()
            if on
        }
        if s.kind in ("stat", "chat", "chapter", "chart", "terminal", "tree", "svg", "drawio"):
            used.add(s.kind)
    for c in cues:
        if c.get("kind") in ("edge", "region", "step"):
            used.add(c["kind"])
        for style in (c.get("style") or "").split():
            used.add(style)
    content = [s for s in w.slides if s.kind not in ("title", "chapter")]
    # Pace: seconds of narration on each content slide; past 20 s a slide reads as a lecture.
    per = [sum(x.duration for x in s.says if x.text) for s in content]
    shapes = [(s.kind, s.visual.get("style"), s.pair.get("kind"), _layout(s)) for s in content]
    # Picture changes: each content slide, and on it each zoom, code or drawing version, and part
    # revealed on a `.build` slide. A pointer moving alone does not count.
    changes = len(content) + sum(
        1
        for s in content
        for x in s.says
        for c in x.cues
        if "zoom" in (c.get("style") or "") or c.get("kind") == "step" or "build" in s.classes
    )
    return {
        "features": sorted(used),
        "slides": len(w.slides),
        "paragraphs": len(says),
        "duration_s": round(sum(x.duration for x in says), 1),
        "s_per_slide": round(sum(per) / len(per), 1) if per else None,
        "long_slides": sum(1 for t in per if t > 20),
        "s_per_change": round(sum(per) / changes, 1) if changes else None,
        "cues": len(cues),
        "inferred_cues": sum(1 for c in cues if c.get("inferred")),
        "kinds": sorted({s.kind for s in w.slides}),
        "kind_counts": dict(Counter(s.kind for s in w.slides).most_common()),
        "plain_share": round(sum(1 for s in content if s.kind in PLAIN) / len(content), 2) if content else None,
        "fit": bool(set(expect) & {s.kind for s in w.slides}) if expect else None,
        "builds": sum(1 for s in w.slides if "build" in s.classes),
        "from_disk": sum(1 for s in w.slides if s.visual.get("file")),
        "status": w.status,
        # How varied the slides look: distinct kind/style/layout shapes per content slide, slides
        # with a layout, a takeaway or icons, and back-to-back slides of the same shape.
        "variety": round(len(set(shapes)) / len(shapes), 2) if shapes else None,
        "layouts": sum(1 for s in content if _layout(s)),
        "takeaways": sum(1 for s in content if s.kind != "text" and s.shown.strip()),
        "statements": sum(1 for s in content if s.kind == "text"),
        "icons": sum(1 for s in w.slides for _ in s.icons),
        "alike": sum(1 for a, b in zip(shapes, shapes[1:], strict=False) if a == b and a[0] not in ("text", "svg")),
        "looks": [look(s) for s in w.slides],
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="sonnet")
    ap.add_argument("--prompts", default=str(ROOT / "eval" / "prompts.json"), help="prompt set (JSON list)")
    ap.add_argument("--runs", default="all", help="comma-separated prompt ids, or all")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--port", type=int, default=4811)
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the data folder")
    ap.add_argument("--rescore", help="recompute the summary and speech checks for this results file (needs --data)")
    ap.add_argument("--data", help="data folder of the run being rescored")
    args = ap.parse_args()
    if args.rescore:
        results = json.loads(Path(args.rescore).read_text())
        expect = {p["id"]: p.get("expect") for p in json.loads(Path(args.prompts).read_text())}
        for r in results:
            w = walkthrough_for(Path(args.data), r.get("url"))
            if w:
                r["summary"] = summarize(w, expect.get(r["id"]))
                if not args.no_asr:
                    r["asr"] = asr_checks(w, Path(args.data) / "audio")
        Path(args.rescore).write_text(json.dumps(results, indent=1, default=str))
        print_table(results)
        return
    await check_browser()
    all_prompts = json.loads(Path(args.prompts).read_text())
    prompts = all_prompts if args.runs == "all" else [p for p in all_prompts if p["id"] in args.runs.split(",")]
    data_dir = Path(tempfile.mkdtemp(prefix="showntell-eval-"))
    jobs = [(p, m) for m in args.models.split(",") for p in prompts]
    sem = asyncio.Semaphore(args.parallel)
    # Speech recognition runs one deck at a time: mlx-whisper's numba layer aborts the whole
    # process when two threads enter it at once.
    asr_lock = asyncio.Lock()
    results = []

    async def one(p, m):
        async with sem:
            print(f"run {p['id']} on {m}", flush=True)
            r = {"id": p["id"], "model": m, "kind": p["kind"], "issues": 0}
            try:
                r.update(await run_agent(p, m, data_dir, args.port, not args.no_play))
                w = walkthrough_for(data_dir, r.get("url"))
                r["summary"] = summarize(w, p.get("expect"))
                if w and not args.no_asr:
                    async with asr_lock:
                        r["asr"] = await asyncio.to_thread(asr_checks, w, data_dir / "audio")
            except Exception:  # noqa: BLE001 - one failed run is a row, not the end of the batch
                r["error"] = traceback.format_exc()[-2000:]
                print(f"failed {p['id']} on {m}:\n{r['error']}", flush=True)
            results.append(r)
            flag = " (timed out)" if r.get("timed_out") else ""
            print(f"done {p['id']} on {m}{flag}: link {r.get('link_after_start')}s after start, cost ${r.get('cost') or 0:.3f}", flush=True)

    await asyncio.gather(*(one(p, m) for p, m in jobs))
    out = ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(results, indent=1, default=str))
    print_table(results)
    print(f"\nresults: {path}\ndata: {data_dir}")
    # stop the eval daemon
    lock = data_dir / "daemon.json"
    if lock.is_file():
        import urllib.request

        d = json.loads(lock.read_text())
        req = urllib.request.Request(
            f"http://127.0.0.1:{d['port']}/api/rpc/shutdown",
            data=b"{}",
            method="POST",
            headers={"Authorization": f"Bearer {d['token']}", "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass
    if not args.keep:
        shutil.rmtree(data_dir, ignore_errors=True)


def print_table(results: list[dict]) -> None:
    head = (
        "| run | model | link s | total s | cost | slides | min | issues | render err | stalls | cue acc | missing/min | preview "
        "| s/slide | s/change | >20 s | small text | plain | fit | variety | layouts | takeaways | icons | alike | features |"
    )
    print("\n" + head + "\n|" + "---|" * (head.count("|") - 1))
    for r in sorted(results, key=lambda r: (r["model"], r["id"])):
        s, pl, a = r.get("summary", {}), r.get("player", {}), r.get("asr", {})
        print(
            f"| {r['id']} | {r['model']} | {r.get('link_after_start', '-')} | {r.get('finished_at', 0):.0f} | "
            f"${r.get('cost') or 0:.2f} | {s.get('slides', '-')} | {s.get('duration_s', 0) / 60:.1f} | {r['issues']} | "
            f"{len(pl.get('render_errors', [])) if pl else '-'} | {pl.get('stalls', '-') if pl else '-'} | "
            f"{a.get('cue_accuracy', '-')} | {a.get('missing_per_min', '-')} | "
            f"{sum(1 for _, n in r.get('tool_calls', []) if n == 'preview_slides')} | {s.get('s_per_slide', '-')} | {s.get('s_per_change', '-')} | "
            f"{s.get('long_slides', '-')} | {r.get('issue_codes', {}).get('text_too_small', 0)} | {s.get('plain_share', '-')} | "
            f"{'-' if s.get('fit') is None else 'yes' if s['fit'] else 'no'} | {s.get('variety', '-')} | {s.get('layouts', '-')} | "
            f"{s.get('takeaways', '-')} | {s.get('icons', '-')} | {s.get('alike', '-')} | {', '.join(s.get('features', [])) or '-'} |"
        )


if __name__ == "__main__":
    asyncio.run(main())
