# Contributing

Thanks for helping. showntell is small on purpose: one Python package, one player, no build step.

## Setup

```zsh
uv sync
uv run pytest -m "not slow and not browser and not mlx and not onnx"   # fast tests; no models or browser
uv run pytest -m "browser or slow"     # Playwright's Chromium and ffmpeg; `uv run playwright install chromium` once
uv run pytest -m "mlx or onnx"         # need the cached voice models
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
node --test tests/js/*.test.js         # player logic
```

Run a daemon without a voice model with `uv run showntell serve --speech fake --port 4800`.

## Where things live

| Path | What |
|---|---|
| `src/showntell/format.py` | the incremental Markdown parser |
| `src/showntell/build.py` | slides from parsed blocks: visuals, code from disk, diffs, images |
| `src/showntell/cues.py` | resolving cues against a slide's visual, and inferring them when narration has none |
| `src/showntell/checks.py` | findings on sizes, finished slides and the whole walkthrough |
| `src/showntell/mermaid.py` | node ids, labels and edges read from Mermaid source |
| `src/showntell/kinds/` | bundled kinds (chart, stat, chat, terminal, tree, svg, drawio), one module each, and their registry |
| `src/showntell/store.py` | walkthrough sessions, speech requests, events, persistence |
| `src/showntell/server.py` | MCP tools, prompts, resources and web routes |
| `src/showntell/app.py` | the daemon: wiring and the request guard |
| `src/showntell/speech/` | speech backends (MLX, ONNX, fake), the worker queue and audio cache |
| `src/showntell/pronounce.py` | turning code and units into speakable text |
| `src/showntell/static/` | the player; `js/timeline.js` is the pure core, `js/kinds/` has one module per slide kind (module map in `docs/architecture.md`) |
| `src/showntell/text/` | everything the agent reads: instructions, tool descriptions, guide; `text/file/` replaces the parts about links for `--deliver file` |
| `src/showntell/export.py`, `video.py` | HTML, folder and MP4 exports; `capture.py` drives the headless player |
| `docs/` | format reference, functional spec, architecture |
| `scripts/eval.py` | end-to-end evaluation with real agents |

## Changes

- **Format changes** go into `docs/format.md` first, keep the "any split at block boundaries" parser property, and must not break existing walkthroughs. The format version lives in `model.py`.
- **Agent-facing text** in `src/showntell/text/` is tuned against the evaluation in `scripts/eval.py`. Re-run the affected prompts and include the before and after table in the pull request.
- **Player changes** need a screenshot of each affected slide kind, and `timeline.js` changes need a `node --test` case.
- **New slide kinds:** see below.
- **Dependencies:** nothing GPL in the default install. Optional extras may be.

## Adding a slide kind

A kind made from a fenced block (```` ```name ````) touches these files:

| File | What goes there |
|---|---|
| `src/showntell/kinds/<name>.py` | `parse(body, attrs)` returning the visual and problems, a size `check`, the noun for a missing cue target, and `KIND`; add it to the registry in `kinds/__init__.py` |
| `src/showntell/static/js/kinds/<name>.js` | drawing, step reveal and any per-frame animation; add it to `KINDS` in `static/js/render.js` (the interface is at the top of that file) |
| `src/showntell/static/player.css` | its styles, with colours only from the theme tokens |
| `src/showntell/text/kinds.json` | the catalogue entry; its `format` is also the fix sent with the kind's `block_error` findings |
| `docs/format.md` | the syntax |

- **Parts:** give every part a cue can point at an id in `visual["ids"]`, in source order. Cue lookup, the reveal-order check and inferred cues then work without builder changes. A kind whose parts are code lines sets `lines=True` and gets `#L` cues and the code size checks instead.
- **Findings:** reuse `block_error` and `block_too_big` unless the kind needs a code of its own; a new code goes in `issues.CODES` and `docs/spec.md`.
- **Agent text:** a line in the guide's visual table and in the `add_slides` description, changed as **Agent-facing text** above says.
- **Tests:** a parser test, a builder test with cues, a golden walkthrough in `tests/js/golden/`, and a browser test.

## Releasing

- **One version:** `pyproject.toml` holds it. After changing it, run `uv run scripts/sync_version.py`; it stamps `src/showntell/__init__.py`, the plugin and marketplace manifests and `gemini-extension.json`, pins the install commands in the plugin, README and `doctor` to `@vX.Y.Z`, and regenerates `GEMINI.md` from the plugin skill. Change the version only when releasing: the pins name a tag that must exist. `tests/test_versions.py` fails while anything disagrees.
- **Generated files:** edit `claude-plugin/skills/showntell/SKILL.md` and `claude-plugin/.mcp.json`, never `GEMINI.md` or `gemini-extension.json`, then re-run the script.
- **Changelog:** rename `## X.Y.Z (unreleased)` to `## X.Y.Z (YYYY-MM-DD)` with at least one entry under it.
- **Tag:** push `vX.Y.Z`. That alone publishes nothing; installs from GitHub pinned to the tag start working.
- **Publish:** publish a GitHub release for the tag (or run `release.yml` on it). The workflow runs lint, format and tests, stops unless the tag equals the pyproject version, the manifests agree and the changelog section is dated and non-empty, then builds, smoke-tests the wheel with `scripts/wheel_smoke.sh` and publishes to PyPI through trusted publishing.
- **Check first:** `uv run scripts/sync_version.py --check --tag vX.Y.Z` runs the same gate locally.
- **Seccomp profile:** `uv run scripts/seccomp_profile.py --check` says whether Docker's default seccomp profile has changed since `deploy/chromium-seccomp.json` was built from it. If it has, rebuild with `--commit latest` and run `scripts/container_check.sh` before tagging.
- **Through Claude Code:** `SHOWNTELL_TEST_CLAUDE=1 uv run pytest -m claude` checks what the model really receives from the tools (the guide, the share-the-link instruction, preview pictures). CI cannot run it; it needs a logged-in `claude` and costs a few cents.
- **Clean room:** `uv run scripts/provenance_check.py <reference folder>` should match only HTML boilerplate.

## Reporting bugs

Include `showntell doctor` output, the walkthrough's `source.md` (in the data folder under `walkthroughs/<id>/`), and what you expected.
