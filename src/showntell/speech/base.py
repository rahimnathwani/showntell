"""Speech backend interface and the English Kokoro voices."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np

SAMPLE_RATE = 24000

# Kokoro v1.0 English voices. The first letter is the language: a = American, b = British.
VOICES = {
    "af_heart": "American female, warm (default)",
    "af_bella": "American female, bright",
    "af_nicole": "American female, soft",
    "af_aoede": "American female",
    "af_kore": "American female",
    "af_sarah": "American female",
    "af_nova": "American female",
    "af_sky": "American female",
    "af_alloy": "American female",
    "af_jessica": "American female",
    "af_river": "American female",
    "am_michael": "American male",
    "am_fenrir": "American male",
    "am_puck": "American male",
    "am_echo": "American male",
    "am_eric": "American male",
    "am_liam": "American male",
    "am_onyx": "American male, low",
    "am_adam": "American male, deep",
    "bf_emma": "British female",
    "bf_isabella": "British female",
    "bf_alice": "British female",
    "bf_lily": "British female",
    "bm_george": "British male",
    "bm_fable": "British male",
    "bm_lewis": "British male",
    "bm_daniel": "British male",
}
DEFAULT_VOICE = "af_heart"

_ACCENTS = {"american": "a", "us": "a", "usa": "a", "british": "b", "uk": "b", "english": "b", "england": "b", "brit": "b"}
_GENDERS = {
    "female": "f",
    "woman": "f",
    "women": "f",
    "girl": "f",
    "feminine": "f",
    "male": "m",
    "man": "m",
    "men": "m",
    "boy": "m",
    "masculine": "m",
}
_FILLER = {
    "a",
    "an",
    "the",
    "voice",
    "accent",
    "accented",
    "narrator",
    "speaker",
    "with",
    "and",
    "please",
    "one",
    "default",
    "english-speaking",
}
_GROUPS = {"af": "American female", "am": "American male", "bf": "British female", "bm": "British male"}


def voice_catalogue() -> str:
    """The voices grouped by accent and sex, with any trait in brackets: for the tool description."""
    out = []
    for prefix, name in _GROUPS.items():
        ids = []
        for vid, about in VOICES.items():
            if vid.startswith(prefix + "_"):
                trait = (about.split(", ", 1)[1] if ", " in about else "").replace(" (default)", "")
                if vid == DEFAULT_VOICE:
                    trait = "default" + (f", {trait}" if trait else "")
                ids.append(f"{vid} ({trait})" if trait else vid)
        out.append(f"{name}: {', '.join(ids)}")
    return "; ".join(out) + "."


def _words(asked: str) -> list[str]:
    return [w for w in re.sub(r"[.,;:()-]+", " ", asked.lower().replace("u.s.", "us").replace("u.k.", "uk")).split() if w not in _FILLER]


def _traits() -> set[str]:
    return {w for v in VOICES.values() for w in re.findall(r"[a-z]+", v)} - {"american", "british", "female", "male", "default"}


def resolve_voice(asked: str) -> str:
    """A voice id from an id (any case), `default`, or a description such as `British male` or
    `American female, soft`: the first voice of that accent and sex with the most of the traits
    asked for. `English` is a British accent only when no other accent is named, since every voice
    speaks English. Raises ValueError naming what exists when the description has a word no voice
    matches, such as another accent."""
    asked = (asked or "").strip()
    if asked.lower() in VOICES:
        return asked.lower()
    words = _words(asked)
    if not words and "default" in asked.lower():  # `default`, `the default voice`
        return DEFAULT_VOICE
    if "english" in words and any(_ACCENTS.get(w) for w in words if w != "english"):
        words = [w for w in words if w != "english"]
    accent = {_ACCENTS[w] for w in words if w in _ACCENTS}
    sex = {_GENDERS[w] for w in words if w in _GENDERS}
    traits = _traits()
    wanted = [w for w in words if w in traits]
    unknown = [w for w in words if w not in _ACCENTS and w not in _GENDERS and w not in traits]
    if not words or unknown or len(accent) > 1 or len(sex) > 1:
        why = f" (no voice is {' '.join(unknown)})" if unknown and words else " (it names two accents or both sexes)" if words else ""
        raise ValueError(f"unknown voice {asked!r}{why}. English voices are American or British, female or male: {voice_catalogue()}")
    picks = [v for v in VOICES if (not accent or v[0] in accent) and (not sex or v[1] in sex)]
    return max(picks, key=lambda v: sum(w in VOICES[v] for w in wanted))  # max keeps the first of equals


def voice_misses(asked: str, voice: str) -> list[str]:
    """The traits a description asked for that the voice chosen for it does not have."""
    traits = _traits()
    return [w for w in _words(asked) if w in traits and w not in VOICES.get(voice, "")]


@dataclass
class Token:
    """A spoken token with its time span, in the order spoken."""

    text: str
    start: float
    end: float


class Backend(Protocol):
    name: str
    model_id: str  # part of the audio cache key

    def load(self) -> None:
        """Load weights and warm up. Called once, on the speech thread."""

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        """Speak `text`; return mono float32 audio at SAMPLE_RATE and timed tokens."""
