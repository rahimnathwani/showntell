"""Findings returned to the agent: a stable code, a severity, where, what is wrong and how to fix it.

An `Issue` is a `str` (the message followed by the fix), so code that only needs the text keeps
working; `to_dict` gives the structured form sent to MCP clients. Every code is listed in
`CODES` and documented in docs/spec.md.
"""

from __future__ import annotations

SEVERITIES = ("error", "warning", "info")

# code: (severity, what it means). Errors lose or misplace content; warnings will look or sound
# wrong; info is advice.
CODES: dict[str, tuple[str, str]] = {
    # source and structure
    "source_error": ("error", "a file, line range or revision could not be read"),
    "image_error": ("error", "an image is missing or not a type the player shows"),
    "block_open": ("warning", "a code block is still open at the end of a call"),
    "block_dropped": ("error", "a code block was never closed and was dropped"),
    "front_matter": ("warning", "a front matter value is invalid and was ignored"),
    "theme_file": ("warning", "the project's showntell.theme.json has a value that was ignored"),
    "extra_visual": ("warning", "a slide got a second visual, which moved to a new slide"),
    "old_without_new": ("warning", "an `old` block had no `new` block after it"),
    "code_body_ignored": ("info", "a `file=` block had a body, which was ignored"),
    "image_no_alt": ("warning", "an image has no alt text"),
    "too_many_slides": ("warning", "the walkthrough is over the slide limit"),
    "too_long": ("warning", "narration is past the length the guide aims for"),
    "revise_kept_first": ("warning", "revise_slide got more than one slide and kept the first"),
    # size
    "code_too_tall": ("warning", "code has more lines than the frame shows comfortably"),
    "code_too_wide": ("warning", "code lines are wider than the frame shows comfortably"),
    "diff_too_tall": ("warning", "a diff side has more lines than the frame shows comfortably"),
    "diff_too_wide": ("warning", "diff lines are wider than the frame shows comfortably"),
    "diff_hunks": ("warning", "a pasted diff has several hunks"),
    "diagram_too_big": ("warning", "a diagram has more nodes than the frame shows comfortably"),
    "generated_file": ("info", "a diff shows a lockfile or generated file"),
    "block_error": ("error", "a line in a chart, stat or chat block could not be read and was left out"),
    "block_too_big": ("warning", "a chart, stat or chat block has more parts than the frame shows comfortably"),
    "tree_error": ("warning", "a file tree's nesting is inconsistent"),
    "icon_unknown": ("warning", "an icon name is not in the bundled set and was left out"),
    "svg_removed": ("warning", "a drawing had elements or attributes the player does not allow, which were removed"),
    # cues
    "cue_off_code": ("error", "a cue points at lines the slide does not show"),
    "cue_no_target": ("error", "a cue names an item, row or node that does not exist"),
    "cue_moved": ("info", "a cue's quoted text was found on another line and the cue moved there"),
    "cue_text_missing": ("warning", "a cue's quoted text is not in the code"),
    "cue_wrong_kind": ("warning", "a cue's target does not suit the slide's visual"),
    "cue_no_visual": ("warning", "a cue is on a slide with no visual"),
    "no_cues": ("info", "a slide with a visual has no cues"),
    "zoom_unused": ("info", "narration names several parts of one picture without zooming onto them"),
    "decode_unused": ("info", "a slide builds in order to a last part that could stay hidden until named"),
    "build_cues_bunched": ("info", "a step-reveal slide fires all its cues in the first paragraph"),
    "build_cue_order": ("info", "a step-reveal slide cues its parts out of source order"),
    # narration
    "no_narration": ("warning", "a slide has nothing to say"),
    "thin_narration": ("warning", "a slide shows more than its narration gives time to read"),
    "reads_screen": ("warning", "narration repeats on-screen text word for word"),
    "same_openings": ("info", "several slides open with the same words"),
    "opening_code_names": ("info", "the opening names code before saying what it is for"),
    "spoken_handle": ("warning", "narration contains a handle, commit hash or URL the voice would read out"),
    "still_slide": ("warning", "the screen does not change for a long stretch of narration"),
    "code_run": ("info", "several code slides in a row with nothing showing behaviour"),
    "plain_run": ("info", "several slides in a row are only lists, tables or text"),
    "same_look": ("warning", "two slides in a row show the same kind of visual the same way"),
    "no_takeaway": ("warning", "code, tables or formulas with no `>` line saying what to take from them"),
    "layout_unused": ("info", "a layout class on a heading has nothing to lay out"),
    "change_as_code": ("info", "a code slide talks about a change but shows one version of the code"),
    "list_dates": ("info", "a list or table of dated events that a timeline would show"),
    "table_numbers": ("info", "a table is mostly numbers a chart would compare at a glance"),
    "stat_no_change": ("warning", "a stat shows a change that is not one: the same value, or every number from 0"),
    "quiz_shape": ("warning", "a quiz does not have two to four choices with exactly one marked right"),
    "quiz_reveal": ("warning", "a quiz's answer is never pointed at, or is pointed at while the question is asked"),
    "narration_label": ("warning", "narration starts with a label such as Narration: that the voice reads aloud"),
    # reported by the open player
    "render_error": ("error", "a diagram, formula or image failed to render in the player"),
    "audio_error": ("error", "narration audio failed to load or be voiced"),
    "player_error": ("error", "the player hit a script error"),
    "text_too_small": ("warning", "a visual's text renders below a comfortable reading size"),
    "overflow": ("warning", "a visual still overflows the slide after shrinking"),
    "pointer_offstage": ("warning", "a cue's target is outside the visible slide"),
    "drawing_overlap": ("warning", "a line runs through a label in a drawing, or two labels overlap"),
    "diagram_shape": ("info", "a flowchart's direction leaves it small in the slide's shape"),
    "slow_render": ("info", "a slide took longer to draw than the player waits"),
    "viewer_waited": ("info", "the viewer caught up with the writer and waited"),
    # coverage
    "no_browser": ("warning", "Playwright's Chromium is not installed, so finish did not draw the slides"),
    "not_checked": ("info", "a check could not run"),
}

PLAYER_CODES = {
    "render_error",
    "audio_error",
    "player_error",
    "text_too_small",
    "overflow",
    "pointer_offstage",
    "slow_render",
    "viewer_waited",
    "drawing_overlap",
    "diagram_shape",
}
MAX_PER_REPLY = 12
MAX_PER_CODE = 3


class Issue(str):
    code: str
    severity: str
    message: str
    fix: str
    slide: str | None
    line: int | None
    source: str

    def __new__(
        cls,
        code: str,
        message: str,
        fix: str = "",
        *,
        slide: str | None = None,
        line: int | None = None,
        severity: str | None = None,
        source: str = "server",
    ) -> Issue:
        if code not in CODES:
            raise KeyError(f"unknown issue code {code!r}")
        message, fix = message.strip(), fix.strip()
        obj = super().__new__(cls, f"{message} {fix}".strip())
        obj.code, obj.message, obj.fix = code, message, fix
        obj.severity = severity or CODES[code][0]
        obj.slide, obj.line, obj.source = slide, line, source
        return obj

    def to_dict(self) -> dict:
        d = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.fix:
            d["fix"] = self.fix
        if self.slide:
            d["slide"] = self.slide
        if self.line:
            d["line"] = self.line
        if self.source != "server":
            d["source"] = self.source
        return d


def as_dict(i) -> dict:
    """Structured form of an issue; plain strings (from older callers) become code `other`."""
    if isinstance(i, Issue):
        return i.to_dict()
    if isinstance(i, dict):
        return i
    return {"code": "other", "severity": "warning", "message": str(i)}


def player_issue(body: dict) -> Issue | None:
    """An issue from a player report. The text is untrusted: kept short and never interpreted."""
    msg = " ".join(str(body.get("message", "")).split())[:300]
    if not msg:
        return None
    code = str(body.get("code", ""))
    if code not in PLAYER_CODES:
        code = "player_error"
    slide = str(body.get("slide", ""))[:64] or None
    return Issue(code, msg, slide=slide, source="player")


def summarize(issues: list[dict], limit: int = MAX_PER_REPLY, per_code: int = MAX_PER_CODE) -> tuple[list[dict], list[str]]:
    """Order by severity, keep at most `per_code` of each code and `limit` in all.
    Returns (kept, notes about what was folded)."""
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    ordered = sorted(issues, key=lambda d: rank.get(d.get("severity", "warning"), 1))
    kept: list[dict] = []
    folded: dict[str, int] = {}
    seen: dict[str, int] = {}
    for d in ordered:
        c = d.get("code", "other")
        seen[c] = seen.get(c, 0) + 1
        if seen[c] > per_code or len(kept) >= limit:
            folded[c] = folded.get(c, 0) + 1
            continue
        kept.append(d)
    notes = [f"…and {n} more `{c}` finding{'s' if n > 1 else ''}" for c, n in folded.items()]
    return kept, notes


def line_text(d: dict) -> str:
    text = d["message"] + (f" {d['fix']}" if d.get("fix") else "")
    mark = "Error: " if d.get("severity") == "error" else ""
    return f"- {mark}{text} ({d.get('code', 'other')})"
