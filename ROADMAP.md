# showntell roadmap

showntell is a local MCP server for narrated slide walkthroughs. A coding agent writes slides and narration; the server voices them on your machine and returns a link that plays within seconds, while the rest is still being written.

Version 1.0 ships when it meets the launch criteria below. Everything else waits until after 1.0.

## Decisions

| Decision | Choice |
|---|---|
| **Name** | showntell. Free on PyPI. The npm name is taken (a dormant React library) and so is the GitHub organisation, so the repo lives under a personal account. USPTO TSDR, checked 16 October 2026: SHOWNTELL (Reg. 4700403, narrative-story software) was cancelled on 2 March 2022 for want of a Section 8 declaration; SHOW 'N TELL (toys) and SHOW&TELL (filmmaker collaboration software) are dead applications. Not a full clearance search. A 2015 university screen-recording tool also used the name. |
| **Licence** | MIT |
| **Slide format** | Markdown-based |
| **Speech** | Kokoro-82M, English. MLX on Apple silicon; ONNX (the timestamped export, which returns per-token durations) everywhere else |
| **First link** | returned only once the opening narration is playable |
| **Privacy** | No hosted service and no telemetry. The first run downloads the voice model from Hugging Face and the language model from GitHub, then the headless browser from Playwright's CDN; nothing else leaves the machine. |

## Launch criteria for 1.0

- **Install:** from an empty cache, install to first link in under 3 minutes on a 100 Mbit/s connection; with models cached, under 10 s.
- **Speed:** the link arrives within 3 s of the start call with the model loaded, on Sonnet.
- **Evaluation:** at least 9 of 10 eval prompts on Sonnet finish with zero render errors and zero stalls at 1× playback.
- **Pointer:** at least 95% of cues resolve to their intended target and fire within 300 ms of the first spoken word of their label, measured against speech-recognition word times.
- **Pronunciation:** at most one mispronounced technical term per minute of narration on the pronunciation test set, measured by speech-recognition round trip.
- **Quality:** no open priority-1 bugs. All tests pass on macOS and on Linux CI with the fake narrator.

## Status (18 October 2026)

Phases 0 to 2 are built, every launch criterion is met, and the repository is public at https://github.com/rahimnathwani/showntell with the gallery at https://rahimnathwani.github.io/showntell/.

| Launch criterion | Measured | Result |
|---|---|---|
| **Install** | empty uv and Hugging Face caches: install 11 s, first speech 52 s including the 327 MB model download | met |
| **Speed** | link 0.66 to 2.07 s after `start_walkthrough` across 10 Sonnet runs, including the first. The opening rides along with the start call, which saves a model turn. | met |
| **Evaluation** | 10 prompts on Sonnet, each played at 1x from the moment the link was playable: 10 of 10 with no stalls and no render errors | met |
| **Pointer** | 100 of 101 cues fired within 300 ms of the word, measured against speech-recognition word times; every cue was located in the transcript (99.0%) | met |
| **Pronunciation** | 0.55 missed technical terms per minute on the 70-sentence set; 0 per minute across the 10 evaluation walkthroughs | met |
| **Quality** | GitHub CI is green: on Linux x86_64, 562 tests, 14 player tests, 3 browser tests, `doctor` voicing with ONNX and 7 ONNX tests; on macOS Apple silicon, 562 tests, `doctor` voicing with MLX and 4 MLX tests | met |

| Phase | Done |
|---|---|
| **0** | clean room, spec, Markdown format, document model, code from disk, packaging, rename, licence files |
| **1** | security, revise, cue inference, checks, `play`, step reveals, pronunciation, MLX and ONNX backends, time-driven player, transcript, seeking, accessibility, shared daemon with start and instance locks, `doctor`, `autostart`, plugin and skill, prompts, resources, tests, evaluation |
| **2** | static export with captions, docs, CI, release and gallery workflows, recorder for demo video, a five-walkthrough gallery made with Opus (links 0.38 to 0.81 s, $0.22 to $0.64 each), README demo |
| **2.5** | ideas adapted from a study of HyperFrames (an HTML-to-video framework): coded findings with fixes, layout findings measured by the open player, timing and coverage checks, slide-ready wait, MP4 export by frame capture, posters, `preview_slides`, themes with contrast tests, charts, terminal sessions and file trees, a slide-kind catalogue, ranges, presenter mode, `<showntell-player>`, pause-aware captions, golden player state and Linux pixel baselines, plugin marketplace and Gemini extension, one version everywhere, a wheel smoke test |
| **2.6** | daemon replaced when its code changes, five eval prompts for the newer kinds, an opt-in test through the real Claude Code CLI, a shorter guide checked by A/B evaluation, layout checks at `finish_walkthrough`, builder and player split into modules with one Python and one JS module per bundled kind, word-level code morphs at a steady scale, reduced motion and announcements for moving cues, sequence-message and row-number cues, screenshot baselines for every kind in every frame |
| **0.1.x** | releases 0.1.0 to 0.1.4: MLX narrowed to the model loader and forward pass, findings that steer agents to the visual that fits (A/B: word-only slides fell from 27% to 2%), timelines, `export_walkthrough` and `--deliver file` for agents in a sandbox or behind a chat app, first-run fixes. 865 fast tests |

Evaluation cost on Sonnet: $0.09 to $0.24 per walkthrough, $1.40 for the ten.

**Owner steps left:** add a PyPI trusted publisher for `release.yml`, then rerun the Release workflow on the `v0.1.4` tag, whose GitHub release is already published (until then the README installs from the tag on GitHub); announce.

## Phase 0: Foundations

### Clean room

- **Reference material:** kept outside the working tree.
- **Provenance check:** runs over the whole tree (README, scripts, tests, static files; vendored libraries excluded).
- **Independent review:** a reviewer who has never seen the reference material reads the spec and all agent-facing text.

### Spec

A short functional spec in our own words, written before any rewrite. It covers:

- **Slide kinds:** every slide kind, including the ones reserved for after 1.0, so the format never needs a breaking change.
- **Pointer cues:** syntax and targets.
- **Frames and limits:** frame sizes and measured limits.
- **Fast first link:** the order of steps that gets the first link out quickly.
- **Install and project root:** the stdio shim, the shared daemon, and how the project root is found.

### Format

- **Markdown layout:** one heading per slide, visuals as fenced blocks, narration as plain paragraphs, and our own pointer-cue syntax. The parser is incremental and lenient.
- **Chunk boundaries:** the end of each append closes every open block except an unclosed code fence, so the opening narration is voiced without waiting for the next chunk. The "any split gives the same result" test stays.
- **Reserved syntax:** citations, pauses, pronunciation overrides, per-slide voice and speed, and camera moves.

### Document model

- **Stable IDs:** each slide is a unit with a stable id, its visual and its narration.
- **Audio files:** named by a hash of model, voice, speed and spoken text, so unchanged narration is never voiced twice.
- **Source:** the raw Markdown source is kept.
- **Versions:** the format and the saved state each carry a version.

### Code read from disk

- **Project root:** from MCP roots, else the stdio shim's working directory, else an explicit argument.
- **Line numbers:** gutters and cues use real file line numbers.
- **Provenance:** a copy of the code and the commit sha are stored with the walkthrough.
- **Git safety:** git runs as plumbing with external diff, textconv and fsmonitor disabled.

### Tools and text

- **Agent-facing text written fresh:** tool names, tool descriptions, server instructions and the authoring guide, written from the spec alone.
- **Format in the tool description:** the essentials needed to write the opening go in the tool description, so the start call stays small. The full guide comes back in the reply to the first append, keeping it off the path to the first link.
- **Tool surface:** structured output (`url`, `status`, `issues`), tool annotations and titles.

### Packaging

- **spaCy model:** downloaded on first run instead of coming from a URL dependency, which PyPI rejects.
- **MLX:** `mlx-audio` is limited to macOS on Apple silicon and pinned exactly. showntell uses two of its calls, `load_model` and the model's forward pass; phonemes, chunking, voices and word timings are its own, shared with the ONNX engine. A test fails loudly when an mlx-audio release changes either call, and at runtime speech falls back to ONNX.
- **No GPL in the default install:** misaki's English path runs without the espeak-ng fallback.
- **Vendored libraries:** sha256 pins in the vendor script.

### Rename and hygiene

- **Rename:** package, command, `SHOWNTELL_*` env vars, data folder, launchd label, MCP server name, README.
- **Repo files:** `LICENSE` (MIT), `THIRD_PARTY_NOTICES`, `.gitignore` covering `.env`, and a `.env.example` listing the options.
- **Existing tests:** count as zero, because they test the parser being replaced.

### Exit

The new format runs end to end, the provenance check is clean, and the first commit is made.

## Phase 1: Version 1.0

### Evaluation first

- **Harness:** five to ten prompts on Sonnet, built before the rest of this phase and re-run after every format or guide change.
- **Measures:** time to link, total time, cost, validation issues, render errors, stalls, cue accuracy and mispronunciations.

### Security

- **IDs and keys:** 128-bit walkthrough IDs, with a separate write key.
- **Web routes:** Host and Origin checks on every route, and JSON-only POSTs.
- **Player reports:** each needs a per-walkthrough report key, and is marked untrusted when passed to the agent.
- **Image sources:** limited to the project root.
- **Beyond localhost:** serving to other machines needs an access token.

### Authoring

- **Code from disk:** built to the Phase 0 rules.
- **Revise:** a tool to replace one slide, re-voicing only what changed.
- **Cue inference:** narration with no cues gets them inferred by matching its words to code identifiers, node labels and list titles. A model that ignores the guide still gets a moving pointer.
- **Checks:**
  - **Pacing:** too little narration for the code shown.
  - **Repetition:** narration reading on-screen text word for word.
  - **Sameness:** repeated opening phrases.
- **Walkthrough files:** `showntell play file.md` voices and plays a Markdown walkthrough without an agent.

### Slide kinds

The nine existing kinds (intro, code, diff, diagram, math, image, cards, bullet and numbered lists, table) plus **step reveals**: diagram nodes, list items and code lines appear as they are mentioned.

### Voice

- **Pronunciation of code:** split camelCase and snake_case; read symbols, units, versions, paths and complexity notation aloud; apply a user word list. Captions still show the original text.
- **Backends:** one speech interface with two implementations, MLX and ONNX.
- **Voices:** one voice per walkthrough.

### Player

- **Time-driven renderer:** the slide state at any moment is a pure function of narration time, so seeking and export work the same way as playback.
- **Live updates:** server-sent events send only the slides that changed.
- **Navigation:** seek anywhere including within a slide, transcript panel, keyboard control.
- **Pace:** an optional pause at the end of each slide.
- **Accessibility:** screen-reader announcements for slide changes, captions, reduced-motion mode, and a checked phone layout.

### Install

- **One-line install:** `uvx showntell`. In stdio mode it starts the shared background server or reuses a running one.
- **Daemon:** a lockfile holding pid, port, version and token, a `/health` route, and a restart when idle if versions differ.
- **First run:** first-run download progress and `showntell doctor`.
- **Claude Code:** a plugin with `.mcp.json` and a skill; MCP prompts for diff, file and concept walkthroughs.

### Testing

- **Fake narrator:** fixed timings, so tests run without a GPU.
- **Server tests:** in-process MCP client tests and a restart test.
- **Player tests:** JS module tests and screenshot tests for every slide kind in every frame.

### Exit

All Phase 1 items are done and the evaluation meets the launch criteria.

## Phase 2: Launch

- **Static export and captions:** a self-contained folder that plays from any static host, with a WebVTT captions file.
- **Docs:** README with a demo, format spec, architecture, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`.
- **Gallery:** four exported walkthroughs on GitHub Pages.
- **Release:** a CI workflow (Linux with the fake narrator, plus macOS) and a PyPI trusted-publishing workflow.
- **Exit:** the launch criteria are checked and recorded. Publishing and announcing (Show HN, r/LocalLLaMA, MCP registries) are the owner's call.

## After 1.0

| Area | Items |
|---|---|
| **Slide kinds** | pan across one large picture on a cue (zoom on a cue shipped with 0.1.5; quiz slides after 0.1.6), scene animation in a vocabulary models already write well (manim-web tested 16 October 2026 and not ready: wrong frames after seeking; SVG with a seekable timeline worked 5 of 5), formula-part pointer |
| **Trust** | citations with a sources panel, open-in-editor links, stale-code marks, syntax checks on code blocks |
| **Voice** | Kokoro's other languages (check which give word timings), voice blends, per-slide voice and speed, second speaker, sentence-level audio for slow CPUs, recording your own voice |
| **Sharing** | MP3 and podcast feed, read view; the pull request action (after 0.1.6) could host the page on GitHub Pages per pull request instead of a zip |
| **Player** | audio-only mode with lock-screen controls, live parameters |
| **Product** | courses, teaching styles, asking a question from the player, MCP Apps inline player, retention and compressed audio, generated images |

## Not planned

- **Background music and sound effects:** studies show they reduce learning.
- **A hosted service:** showntell runs on your machine; static export covers sharing.
