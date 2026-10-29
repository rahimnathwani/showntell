"""The walkthrough document model and its JSON forms (saved state and what the player sees)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

FORMAT_VERSION = 1  # docs/format.md
STATE_VERSION = 1  # layout of walkthrough.json

FRAMES = {"landscape": (1280, 720), "square": (960, 960), "portrait": (720, 1280)}


@dataclass
class Say:
    id: str
    text: str = ""  # display text, as captions show it
    code: list = field(default_factory=list)  # display ranges written as `code`
    overrides: list = field(default_factory=list)  # (start, end, spoken)
    notes: list = field(default_factory=list)  # footnote ids
    cues: list = field(default_factory=list)  # resolved cues; "t" is filled once voiced
    pause: float = 0.0  # a silent pause instead of speech
    status: str = "pending"  # pending, ready, error
    key: str = ""  # audio cache key
    duration: float = 0.0
    words: list = field(default_factory=list)
    error: str = ""


@dataclass
class Slide:
    id: str
    title: str = ""
    kind: str = "text"  # title, code, diff, diagram, math, table, list, image, chart, terminal, tree, text
    classes: list = field(default_factory=list)
    shown: str = ""  # on-screen text from > quotes
    visual: dict = field(default_factory=dict)
    says: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    rev: int = 1
    line: int = 0  # where the slide starts in the walkthrough's Markdown
    pair: dict = field(default_factory=dict)  # the second visual of a `.pair` slide: {kind, visual}
    icon: str = ""  # the heading's {icon=name}
    icons: dict = field(default_factory=dict)  # name -> inner SVG of every icon the slide uses


@dataclass
class Walkthrough:
    id: str
    key: str  # write key
    report_key: str
    title: str
    description: str = ""  # one or two sentences for lists, the gallery and exported files
    frame: str = "landscape"
    theme: str = "dark"
    tokens: dict = field(default_factory=dict)  # project colour overrides, already checked
    voice: str = "af_heart"
    speed: float = 1.0
    root: str | None = None
    sha: str | None = None
    created: float = 0.0
    updated: float = 0.0
    status: str = "writing"  # writing, finished
    questions: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)
    slides: list = field(default_factory=list)
    link_shared: bool = False
    guide_sent: bool = False
    # Hosted mode only (docs/hosted-design.md): who made it, who else may view it, when it was last seen.
    owner: dict = field(default_factory=dict)  # {iss, sub, email}; empty in local mode
    shares: list = field(default_factory=list)  # emails, lower case
    company: bool = False  # shared with everyone who can sign in
    viewed: float = 0.0
    format: int = FORMAT_VERSION
    version: int = STATE_VERSION

    # -- saved state ------------------------------------------------------------------

    def to_state(self) -> dict:
        return asdict(self)

    @classmethod
    def from_state(cls, d: dict) -> Walkthrough:
        known = {f.name for f in fields(cls)}
        w = cls(**{k: v for k, v in d.items() if k in known and k != "slides"})
        for s in d.get("slides", []):
            says = [Say(**{k: v for k, v in x.items() if k in {f.name for f in fields(Say)}}) for x in s.get("says", [])]
            sd = {k: v for k, v in s.items() if k in {f.name for f in fields(Slide)} and k != "says"}
            w.slides.append(Slide(**sd, says=says))
        return w

    # -- what the player sees ---------------------------------------------------------

    def public(self) -> dict:
        return {
            "format": self.format,
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "frame": self.frame,
            "theme": self.theme,
            "tokens": self.tokens,
            "status": self.status,
            "questions": self.questions,
            "notes": self.notes,
            "slides": [public_slide(s) for s in self.slides],
        }


def public_slide(s: Slide) -> dict:
    out = {
        "id": s.id,
        "rev": s.rev,
        "kind": s.kind,
        "title": s.title,
        "classes": s.classes,
        "shown": s.shown,
        "visual": s.visual,
        "says": [public_say(x) for x in s.says],
    }
    for k in ("pair", "icon", "icons"):  # only when used, so older walkthroughs read the same
        if getattr(s, k):
            out[k] = getattr(s, k)
    return out


def public_say(x: Say) -> dict:
    out = {"id": x.id, "status": x.status}
    if x.pause:
        out["pause"] = x.pause
        return out
    out.update(text=x.text, cues=x.cues, notes=x.notes, code=x.code)
    if x.status == "ready":
        out.update(audio=f"audio/{x.key}.wav", duration=x.duration, words=x.words)
    elif x.status == "error":
        out["error"] = x.error
    return out
