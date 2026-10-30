# Changelog

## 0.2.2 (unreleased)

- **Deleted walkthroughs stay deleted:** speech callbacks, open event streams and tool calls still held a deleted walkthrough, and the next audio, save, finish or export wrote its folder back, owner, company switch and shares included, so viewers got it back. A deleted session (by the owner or by retention) now writes nothing, its pending save is cancelled, open event streams end, and a finish or export in flight replies "unknown handle" and leaves no export behind.
## 0.2.1 (2026-10-29)

- **Claude Code can sign in to a hosted server:** Claude Code catches the sign-in on a localhost port it picks each time, and the authorization server compared redirect URIs exactly, so a second sign-in was refused ("Redirect URI ... not registered for client"). Loopback HTTP redirect URIs now match on any port, as RFC 8252 requires; scheme, host, path and query must still match.
- **Hosted walkthroughs no longer stick after a finding:** before each `add_slides`, hosted mode copies the builder so a call over a limit can be undone, and copying failed on any finding left from the previous call, even an info hint, so every later add on that walkthrough failed with an internal error. Findings now copy and pickle with all their fields. Local mode never took that copy.
- **Tried with Entra:** a hosted server on localhost signed in through a real Entra tenant, in the browser and from Claude Code, and made walkthroughs. Google and claude.ai connectors are still tested only against the fake provider.

## 0.2.0 (2026-10-29)

- **Sign-in not yet tried against real providers:** hosted mode's sign-in and MCP authorization pass their tests against a fake OIDC provider, including a full MCP client flow, but nobody has yet signed in through real Google or Entra or added a hosted server to claude.ai. Try it before people rely on it, and report what breaks.
- **Hosted mode, phase 1** (`SHOWNTELL_MODE=hosted`, docs/hosted-design.md): one server for several people. Walkthroughs belong to their maker and open for others only when shared (`share_walkthrough`, to named emails or the whole company); anyone else gets 404, and a removed share ends an open player's live updates. Slide content cannot read the server's disk or reach the network: `project_dir`, `file=`, `rev=` and images are refused with a fix, and the server's own Chromium aborts requests to any other origin. A Content-Security-Policy and CSRF tokens on every page, the RPC route and cross-site embedding off, limits per user (narration, slides, walkthroughs, storage, calls a minute, retention) and speech served round-robin across users. Sign-in is a stub for trials on one machine (`SHOWNTELL_DEV_USERS`); provider sign-in is phase 2.
- **Hosted mode, phase 2:** sign-in with Google, Entra or any OIDC provider (`SHOWNTELL_OIDC`, the `hosted` extra), with the identity and domain rules checked at every sign-in and refresh. showntell is the OAuth authorization server for its own `/mcp` (MCP authorization of 2026-07-28): client ID metadata documents and dynamic registration, PKCE S256, audience-bound opaque tokens with rotating refresh tokens, and a consent page per client. Owners share from the player's settings menu; the gallery has Sign out.
- **Hosted mode, phase 3:** a Dockerfile and a compose file with Caddy, [docs/self-hosting.md](docs/self-hosting.md) with the Google and Entra steps, and `showntell doctor` rows for the hosted settings, the provider and Chromium's sandbox. Hosted mode keeps Chromium's sandbox on; the container runs it under Docker's default seccomp profile plus four calls. Not yet tried against real Google or Entra.
- **Speech queue:** one priority queue per user, served in turn; a single user sees no change.
- **No inline scripts:** the player's head scripts and the gallery's script are files (`static/boot.js`, `static/gallery.js`); single-page exports still inline them.
- **Hostile input:** an SVG block nested thousands of levels deep no longer fails the call (drawings stop at 100 levels); unclosed `_` emphasis no longer makes parsing quadratic; a compressed draw.io page may expand to 20 MB at most.

## 0.1.9 (2026-10-28)

- **Camera on the narration clock:** `{.zoom}` moves are worked out from the clock instead of CSS transitions, so seeking lands mid-move and MP4 export shows the move (it used to cut). Moves are planned against the speech: in over about 1.6 s, starting about 0.4 s into the sentence and settling as the cued word is heard; out in 1 s, front-loaded; a move between close-ups at a similar zoom pulls back about 6% on the way. A held close-up creeps about 2% in, a still drawing breathes about 1%; MP4 export leaves this idle motion out so held frames are reused, and `--motion` keeps it. A zoom onto a large part (a panel) fills about 90% of the frame and needs no pointer; small targets still fill about 60%. The pointer waits while the camera moves and appears on the new subject when it lands. Reduced motion cuts and holds still.
- **Scene changes land on the first word:** a slide starts coming in 0.45 s before its first word, during the silence after the last slide, and has landed (0.5 s) as the word is heard. Cards, numbers and rows arrive 0.1 s apart.
- **Captions one line at a time:** five to seven words, paged whole at a clause or sentence break; spoken words light, the current word brightest, the rest of the line dim. The caption no longer grows to three or four lines and squeezes the stage.
- **`{.highlight}`** sweeps a marker across its target, left to right, and the previous one fades about a second later.
- **`{.decode}`:** a cue style that keeps its target's label scrambled from the start of the slide until the cue, then resolves it left to right in 0.2 s: for the answer the narration builds to. Works on drawing parts, diagram nodes, list items and table rows.
- **Findings `zoom_unused` and `decode_unused`** (info, once per walkthrough): a drawing or screenshot whose narration names three or more of its parts with no zoom is pointed at close-ups; a diagram, table or labelled drawing cued in order to its last part is pointed at `{.decode}`. `text_too_small` on a drawing or draw.io diagram also suggests zooming.
- **Guide:** a section on one picture as many shots (a drawing split into titled panels, named along one axis, with a shared element at the centre), changing the picture about every ten seconds with two or three kinds of visual, and closing a teaching piece on three takeaway cards. It ends on a worked example: a 2×2 drawing of panels round a shared centre, zoomed panel by panel.

## 0.1.8 (2026-10-27)

- **Memory on Apple silicon:** the MLX voice kept every freed GPU buffer for reuse, and sentences of different lengths leave buffers of different sizes, so the cache grew to most of the Mac's memory (25 GB of 32 after ten sentences) and stayed there while the daemon ran. Macs swapped and froze. It is now cleared after each chunk: the cache stays near zero, about 3 GB at peak, and synthesis is faster (80 sentences in 33 s instead of 92 s on an M1 Max).

## 0.1.7 (2026-10-26)

- **Code stays readable:** code and diffs no longer shrink to fit their longest line, which left a six-line diff with one long line at about 11 px on a 1080p frame. A side-by-side diff that would be too small stacks its panes, before above after, and lines too long for the reading size wrap, continuing under their own indentation. Only too many lines make code small now, and `text_too_small` fires on the rendered size whether or not the layout shrank anything, naming how many lines fit.
- **Pointer lead:** cues fire 0.3 s before their word is spoken, about as long as the pointer takes to glide over, so it lands as the word is heard.
- **Quiz slides:** `.quiz` on a heading turns `- [ ]` / `- [x]` items into a multiple-choice question, lettered A to D. Viewers can pick a choice; the answer shows when a cue points at the right one, after an optional `{pause=3}` paragraph that runs a thinking bar. Findings: `quiz_shape`, `quiz_reveal`.
- **Fix before done:** when slides still have errors or warnings at finish, the `finish_walkthrough` reply names them and asks for fixes before telling the user.
- **Guide:** one point per slide in one to three short paragraphs (about 8 to 20 s), concept pieces of one to two minutes, a one-sentence opening, a rule for analogies, a section on reviews that judge (verdict first, a finding per slide with cued evidence and a severity, ranked at the end), quizzes for teaching pieces, and zoom cues across one dense picture. A new `review` prompt. In eval round 5 (Sonnet, the six round 4 prompts) narration per content slide fell from about 21 s to 17 s and five of six pieces ended on a quiz; the new review prompt produced a verdict, two should-fix findings with evidence and a ranked table, and both findings were real (fixed below).
- **GitHub Action:** the repository is an action (`action.yml`) that voices a walkthrough's Markdown on the runner with ONNX, uploads the page and optionally an MP4, and keeps one pull request comment with the outline, transcript and download link up to date. CI runs it on its own example. See docs/github-action.md.
- **Adding the video extra takes effect:** a running daemon started without Playwright is replaced by the same code with it, instead of being reused until it stopped. The browser hint names the export command as well as `mcp`.
- **Eval:** seconds of narration per slide, slides over 20 s and `text_too_small` counts per run; a `review` prompt.
- **A missing browser is loud:** the base install has no Playwright Chromium, so `finish_walkthrough` was skipping its layout check and saying so only in an info line. It now reports `no_browser` as a warning naming what the reply lacks (layout findings, pictures of alike slides, the poster); `showntell doctor` has a `slide checks` row with the two commands that add the browser; the daemon logs it once; the README's install section says how to install with it.

## 0.1.6 (2026-10-22)

- **Poster:** finishing keeps a picture of the title slide as `/w/{id}/poster.png`: the gallery shows it as a thumbnail, the live page carries it as `og:image`, and a folder export without a browser reuses it. Revising the title slide drops it until the next finish.
- **Description:** `start_walkthrough` takes a `description` (also front matter `description:`), shown in `list_walkthroughs` and the gallery and written into exports: `<meta name="description">` and `og:` tags on the HTML, title and description tags in the MP4. The title hint asks for keywords first, under about 50 characters.
- **Length:** `add_slides` warns once (`too_long`) when the narration passes four minutes, the top of the guide's range, so the agent wraps up or splits the topic instead of finding out at finish.
- **Eval:** `scripts/eval.py` refuses to start without headless Chromium, kills an agent run after ten minutes, keeps a failed run as a row, and runs speech recognition one deck at a time; a browser that would not launch had silently emptied the layout and player columns.
- **Fixes:** a daemon restarted right after another stopped kept the port (4747) instead of moving to the next one; the free-port probe now binds as the server does, past sockets left in TIME_WAIT.

## 0.1.5 (2026-10-22)

- **Design:** bundled faces (Inter for text, Bricolage Grotesque for titles, JetBrains Mono for code), larger titles with a kicker (slide number and part), a six-colour palette per theme (`--c1` to `--c6`) for cards, numbers, takeaways, chart series and diagram shapes, a faint dot grid on the stage, a motif behind the title slide and the part number drawn large on chapter cards.
- **Layouts:** `.side` (takeaways beside the visual), `.pair` (two visuals on one slide, cues reach either) and `.full` (the visual edge to edge). A `>` line under a heading is shown as its takeaway, and a slide with no visual is a large statement.
- **Icons:** the Lucide set is bundled: `:name:` before a list item, stat label or `>` line, `{icon=name}` on a heading, `<use href="icon:name"/>` in a drawing. `icon_unknown` names close ones.
- **Filling the slide:** code, tables, lists, numbers and exchanges grow into the space they have, and diagrams up to 2.2 times their drawn size.
- **Motion:** slides rise in, alike slides in a row slide across like pages, title and chapter slides zoom in, and cards, numbers, rows and takeaways arrive one after another. `.morph` makes a drawing glide from the previous slide's. All of it follows the narration's clock, so MP4 export shows it.
- **Charts and pictures move:** bars grow from the axis and lines draw in as the slide arrives; a picture without region cues slowly pushes in.
- **Themes:** `night` (indigo, pink), `ocean` (teal, coral) and `studio` (bright, violet) join dark, light, paper and contrast.
- **Seeing the deck:** the `finish_walkthrough` reply lists each slide's look (kind, layout, takeaway, icons), names every run of slides that look alike and shows pictures of them, asking for one in each run to change; `same_look` and `no_takeaway` are now warnings; `scripts/eval.py` scores the same (variety, layouts, takeaways, icons, alike pairs).
- **`diagram_shape`:** a top-to-bottom flowchart left narrow on a wide slide, or the reverse, is reported with the direction to use.
- **Fixes:** quoted-text cues on a `.pair` slide find text in either visual; seeking or an export worker landing inside a transition now shows the slide going out too.
- **Findings:** `same_look` (two alike slides in a row), `no_takeaway` (dense slides with no `>` line) and `layout_unused` (a layout class with nothing to lay out). The guide has a section on giving each slide its look.

## 0.1.4 (2026-10-18)

- **First run:** when the daemon and `showntell doctor` downloaded the spaCy model at the same time, one could pull the other's half-unpacked folder away (FileNotFoundError on `tok2vec/model`). Each now unpacks into its own folder.
- **Speech recovers:** after the voice model fails to load, the next narration tries again (at most every 30 s) instead of failing until the daemon restarts.

## 0.1.3 (2026-10-18)

- **Export from the agent:** a new `export_walkthrough` tool writes an MP4 or one offline HTML page, by default into the project folder named after the title, after waiting for the narration. `max_mb` shrinks an MP4 to fit, first by lowering the audio bitrate and then the picture to 720p, or fails with a clear message.
- **File delivery:** `showntell mcp --deliver file` (or `SHOWNTELL_DELIVER=file`) is for agents in a sandbox or behind a chat app such as WhatsApp. The agent never sends the link: it writes the whole walkthrough, exports an MP4 kept within 16 MB, and sends the file. The mode belongs to each MCP connection.
- **`showntell export --max-mb`:** the same size limit on the command line.
- **`doctor`:** reports whether ffmpeg and Chromium are there for MP4 export.

## 0.1.2 (2026-10-18)

- **Choosing visuals:** new findings while the agent writes: three word-only slides in a row (`plain_run`), a change shown as plain code (`change_as_code`), a table of numbers (`table_numbers`), a stat with no real change (`stat_no_change`) and script labels such as "Narration:" that the voice would read (`narration_label`). `code_run` now arrives while writing instead of only at finish. The `add_slides` description and the guide say which visual suits which idea.
- **Timelines:** `.timeline` on a list's heading draws dated events on a line (`- **1989**: what happened`), across a landscape slide and down a tall one; `list_dates` suggests it for a list or table of dates.
- **Diffs:** in a walkthrough whose title or opening names a commit, range or pull request, the first `file=` code slide before any diff gets the `change_as_code` hint.
- **Evaluation:** four prompts that name no visual (a commit review, a history, a business decision, compound interest); every run is scored for its share of word-only slides and whether the visual the prompt calls for appears.

## 0.1.1 (2026-10-17)

- **MLX speech:** uses only mlx-audio's model loader and forward pass. Phonemes, chunking, voices and word timings are shared with the ONNX engine, so an mlx-audio release can no longer break them, and both engines time words the same way.
- **Word timings:** after a word misaki cannot pronounce, MLX no longer shifts the later words in that stretch of speech or drops its last word.
- **`espeak` extra:** now used by the ONNX engine too, not only MLX.

## 0.1.0 (2026-10-17)

First public release.

- **Format:** Markdown walkthroughs with code, diffs, Mermaid diagrams, KaTeX formulas, tables, lists, cards, steps and images; pointer cues as links; step reveals with `.build`; pauses, footnotes and pronunciation overrides.
- **Agents:** MCP tools to start, add slides, revise a slide, finish and list; prompts for diffs, files and topics; resources holding each walkthrough's Markdown; problems reported back on every call; cues inferred when narration has none.
- **Code from disk:** `file=` and `lines=`, and diffs from git revisions, with real line numbers. Git runs only plumbing commands.
- **Speech:** Kokoro-82M on MLX (Apple silicon) or ONNX Runtime (int8, any CPU), 27 English voices, no GPL dependencies by default. Code, units, versions and several hundred technical terms are pronounced properly; unknown words are never skipped.
- **Player:** live per-slide updates, a pointer timed to the word, captions, transcript, chapters, seeking, keyboard control, pause after each slide, step reveals, screen-reader announcements, reduced motion, phone layout. Missing audio plays silently instead of stopping.
- **Daemon:** shared across MCP clients through `showntell mcp`; lockfile, health route, replacement when idle on upgrade; `doctor`, `play`, `export`, `list` and macOS `autostart` commands.
- **Export:** static folders with WebVTT captions and optional MP3 audio; a gallery builder; a recorder for MP4 and GIF demos.
- **Install paths:** a Claude Code plugin marketplace in this repository, a Gemini CLI extension, and one version stamped into every manifest by `scripts/sync_version.py`.
- **Security:** Host and Origin checks on every route, JSON-only POSTs, 128-bit ids with separate write and report keys, access tokens beyond localhost.

- **Start time and access token:** `?at=90` starts the player 90 s in. `?t=` is only the access token on links served beyond this machine; a token that began with digits used to start the walkthrough part-way through.
- **Revising rewrites the source:** a revised slide's Markdown takes the old slide's place, so the walkthrough's resource and `source.md` show each slide once and build the same slides again.
- **Findings without delay:** problems with the last slide of an `add_slides` call come back in that call's reply, not the next one.
- **Durations on the player's clock:** slide start times and lengths in `finish_walkthrough`, `list_walkthroughs` and `showntell list` match the player, chapter cards and pauses between paragraphs included.
- **Pull request ranges:** `rev=A...B` compares `B` with the commit where it branched from `A`.
- **Stray list markers:** a narration paragraph that starts with `1. ` or `- ` after the slide's visual is spoken, not made into a second visual.
- **Project folder in the reply:** `start_walkthrough` says which folder `file=` and `rev=` read from.
- **Exporting a Markdown file** waits for its audio without drawing every slide in a browser on each check.
- **Theme switch keeps your place** in the walkthrough.
- **Clearer daemon errors:** a plain-text refusal from the daemon reaches the agent as a message, not a JSON error.
- **Pinned installs:** README, plugin and Gemini extension install `@v0.1.0` from GitHub; `scripts/sync_version.py` keeps the pins on the current version. Pushing a tag no longer publishes to PyPI; publishing a GitHub release does, after lint, format and tests pass.
- **Pruning:** `showntell prune --older-than 60d` deletes walkthroughs unchanged for that long and cached audio nothing uses (`--dry-run` lists them); the daemon log stays under 5 MB.
- **Shared folders only:** when the MCP client shares its folders, `project_dir` must be inside one of them.
- **Speech fallback:** on Apple silicon, speech switches to ONNX when MLX fails to load or speak, instead of failing.
- **Windows:** marked untested, best effort.
- **Formatting:** the code is formatted with `ruff format` (line length 140), checked in CI.

- **Drawings that move:** consecutive ```` ```svg step ```` blocks are versions of one drawing; at `#step-2`, elements that keep their id glide to their new place, size, angle and colour on the narration's clock, so seeking and video show the motion exactly.
- **Hand-drawn drawings:** ```` ```svg sketch ```` redraws shapes with Rough.js and text in the Virgil face, as on a whiteboard.
- **Overlap check:** the player reports labels a line runs through, or that sit on each other (`drawing_overlap`).
- **Drawings past their edge:** a drawing with parts outside its viewBox, which spill past the slide, is reported (`overflow`).
- **Row ids:** a table row's `{#id}` is taken from any cell, not only the last.
- **Video keeps the motion:** MP4 export and posters now capture the travelling dot, counting numbers and checklist ticks frame by frame; before, a frame was reused while the slide's cue stayed the same.
- **One-file export:** `showntell export ID talk.html` writes a single page that plays offline from a double-click, with the player, slides, MP3 narration and only the libraries its slides use (a four-minute walkthrough with code comes to about 3 MB, mostly narration; diagrams add about 1 MB and formulas 0.7 MB). It makes no network requests.
- **draw.io diagrams:** ```` ```drawio file=docs/arch.drawio ```` shows a page of a `.drawio` file (plain or compressed) or a `.drawio.svg` export, drawn by the official draw.io viewer, bundled and loaded only when needed, with no network access. Cues point at cells by id or label, shapes reveal in order with edges following their ends, and links, outside images, web fonts and maths are removed first.
- **Drawings:** an ```` ```svg ```` block, inline or read with `file=`, draws what code and boxes can't: light, orbits, lenses, layouts. Elements with ids take cues and reveal in steps, strokes draw themselves in, and theme colours are available as `var(--fg)` and friends. The builder keeps only drawing elements and attributes; anything that could run or fetch is removed and reported (`svg_removed`).
- **Chart callouts:** a note on the tallest bar no longer crowds the title and the value label.

- **Layout checked at finish:** with Playwright installed, `finish_walkthrough` draws every slide fully revealed in headless Chromium, so findings such as `text_too_small` and `overflow` reach the agent before it stops, without anyone opening the link (`SHOWNTELL_FINISH_CHECK=0` turns it off).
- **Shorter guide:** the authoring guide went from about 2,470 to 1,830 words, checked against the old one by A/B evaluation; a chart, stat, chat or tree block that fails to parse now returns its format in the finding.
- **More cue forms:** `#Sender->>Receiver` points at a message in a sequence diagram, and `#row-N` works on tables whose rows have ids.
- **Clearer codes:** problems in chart, stat and chat blocks are `block_error` and `block_too_big` (were `chart_error` and `chart_too_big`).
- **Smoother code steps:** words that stay glide within edited lines, and the scale holds still between versions.
- **Reduced motion and screen readers:** with reduced motion, numbers show their value at once and the travelling dot rests at the end of its route; the route of an edge path and the target of a zoom are announced.
- **Charts in every frame:** charts are drawn to the frame's shape, so portrait and square charts fill the slide.
- **Daemon updates:** a daemon running older code is replaced once idle, even when the version number is the same; a newer one is used as it is, so two installs never replace each other in turn. `showntell stop` stops it.
- **Internals:** the builder is split into cue resolution, checks and Mermaid reading, and the player into modules; each bundled slide kind is one Python module and one JS module (see CONTRIBUTING, "Adding a slide kind").

- **Code that evolves:** consecutive `step` code blocks are versions of one piece of code; `#step-2` morphs to the next, with unchanged lines gliding into place.
- **Long code:** `scroll` keeps up to 400 lines at reading size, scrolls to each cued range and dims the rest.
- **Packets on diagrams:** `#a->b->c` sends a dot along flowchart edges, one hop at a time; edges on step-reveal diagrams draw themselves in.
- **Pointer styles:** `{.circle}`, `{.underline}`, `{.arrow}` and `{.highlight}` after a cue draw hand-drawn marks; `{.zoom}` moves the camera onto the target.
- **Numbers:** a `stat` block shows up to four headline numbers that count from before to after when named.
- **Exchanges:** a `chat` block shows a conversation or request and response, message by message.
- **Images:** `{frame=browser|window|phone}` draws device chrome; cues point at a region with `#@left,top,width,height`.
- **Chart notes:** `note …:` lines add callouts that cues can name.
- **Chapters, stacks, checklists:** `.chapter` headings make numbered part cards; `.stack` draws a list as layers; `[x]`/`[ ]` items make a checklist whose ticks draw in.

- **Structured findings:** every problem returned to the agent has a stable code, a severity, the slide, the Markdown line and a separate fix; replies list errors first, at most twelve, three per code. `docs/spec.md` lists every code.
- **New checks:** narration that reads out handles, URLs or commit hashes; step-reveal cues bunched in one paragraph or out of source order; four code slides in a row; an opening that names code first; diffs of lockfiles and generated files; slides that hold still for over 20 s once voiced. `finish_walkthrough` says which checks could not run and lists slide start times.
- **Player findings:** the open player reports text shrunk below a readable size, content that overflows, cues pointing off the slide, slides slow to draw, and viewers left waiting; errors before the player starts are reported too.
- **Slide-ready wait:** narration for a new slide waits up to 1.5 s for its diagram or formula to finish drawing.
- **New slide kinds:** ```` ```chart ```` (bar, hbar, line), ```` ```terminal ```` and ```` ```tree ````, with cues, step reveals and checks; `showntell://slide-kinds` describes every kind with an example.
- **Themes:** dark, light, paper and high contrast, all meeting WCAG text contrast; chosen by `theme`, front matter, or the project's `showntell.theme.json`, and switchable by the viewer.
- **preview_slides:** a tool that returns pictures of slides, fully revealed, for the agent to check.
- **Ranges and presenting:** `?from=slide&to=slide` plays a range and stops, and `revise_slide` links to the revised slide alone; "Pause after each step"; `?present=1` steps through by hand without the voice, with notes and an audience window; a menu item copies a change request for the current slide.
- **Embedding:** `static/embed.js` defines `<showntell-player>` with events, `play`, `pause`, `seek` and a lazy poster.
- **Captions:** WebVTT phrases break where the voice pauses or a sentence ends.
- **Tests:** golden player state for three walkthroughs, Linux pixel baselines (`scripts/update_baselines.sh`), a contrast test for every theme.
- **Idle cost:** the player's animation loop stops while paused.

- **MP4 export:** `showntell export ID OUT.mp4` captures every frame by seeking the player in headless Chromium: H.264 at 30 fps (`--fps`), 1920x1080 for landscape, AAC narration placed on the timeline, captions as a mov_text track (`--burn-captions` to draw them too), parallel workers (`--workers`), duration checked against the timeline. 13 to 16 times real time on an M-series Mac. Needs the new `video` extra (Playwright) and FFmpeg.
- **Capture mode:** `?capture=1` on the player hides everything but the stage, plays no audio, turns animation off and exposes `window.__showntell.duration()` and `seek(t)`. `showntell.capture.capture_frames` returns PNG frames at given times.
- **Posters:** folder exports write `poster.png`, the title slide, and reference it in `og:image` and `twitter:image` tags, when Playwright is installed.
- **Timeline:** the exporter's paragraph lengths now match the player's exactly for paragraphs without audio.
