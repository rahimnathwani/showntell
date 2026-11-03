---
name: showntell
description: Make a narrated slide walkthrough, voiced locally, with the showntell MCP tools. Use when the user asks to be walked through code, a file, a diff, commit range or pull request, an architecture or data flow, a concept, algorithm or piece of maths, or asks for a narrated, visual, spoken or video-style explanation. Not for quick factual answers or questions a few lines of text settle.
---

# showntell walkthroughs

The MCP tools carry the format: the `add_slides` description has the essentials, and the first playable reply brings the full guide with this frame's limits. Follow them, not memory.

## Choose what to cover

One question per walkthrough, 6 to 12 slides, two to four minutes: purpose, then mechanism, then consequences.

| Request | Cover |
|---|---|
| **Diff or PR** | why it changed, the edits that matter in reviewer order, risks, how to test |
| **File** | its job, main parts in control-flow order, usage, pitfalls |
| **Architecture** | components, then one request's path through them |
| **Concept, maths** | the problem, how it works, trade-offs, real project code if any |

For a broad request, cover what the user asked about and offer the rest as end questions.

## Start first

Call `start_walkthrough` before opening a file or running `git`: the opening plays while you read. Write it to hold whatever you find: the question, the goal or why it matters, and any fact the request gives; what you have yet to read goes in later slides. Once the link is shared, read what the first content slide needs and send it; read for the rest between calls, a little at a time, since a long read stalls playback.

## Order of calls

1. **start_walkthrough with the opening:** a title, `project_dir` as the absolute repo path so `file=` resolves, and `opening`: the title slide and one or two spoken sentences, nothing more. `frame: portrait` for phones. Without `opening`, the first `add_slides` call must carry it instead.
2. **Share the link:** as soon as the reply says playable, send the link in a message before any other tool call. If it says audio is still being made, share it anyway. The same reply carries the full guide.
3. **add_slides, body:** the first content slide alone and short, so it arrives within seconds; then one to three whole slides per call, never resending Markdown. Fix reported problems in later slides, or with `revise_slide`.
4. **finish_walkthrough:** two or three specific follow-up questions the viewer might ask next.
5. **Tell the user:** finished, with the link, in a line or two. No script recap.

## Show real code

- **Files:** a code block with `file=path lines=40-56` and an empty body shows the file with its own line numbers. Cue those numbers.
- **Changes:** a `diff` block with `file=path rev=A` compares commit A with the working tree; `rev=A..B` compares two commits.
- **Never retype** code that is on disk or in git.

## Later changes

`list_walkthroughs` finds an earlier handle, the resource `showntell://walkthroughs/{id}` holds its Markdown, and `revise_slide` replaces one slide.
