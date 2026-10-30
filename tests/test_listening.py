"""Narration measured as heard: sentence length in spoken words and numbers close together."""

from __future__ import annotations

import json
import time
from pathlib import Path

from showntell.checks import LONGEST_SENTENCE, MEAN_SENTENCE, NUMBERS_MIN, NUMBERS_PER_100
from showntell.listening import heard

GALLERY = Path(__file__).resolve().parents[1] / "gallery"
OPENING = "# How caching works\n\nA cache keeps answers close.\n"
LONG = (
    "The loader reads the manifest from disk, checks every entry against the cache, fetches the ones that are "
    "missing from the network in parallel, and then writes them back so that the next start can skip the whole step."
)


def gallery_paragraphs() -> list[tuple[str, list]]:
    return [
        (x["text"], x.get("code", []))
        for f in sorted(GALLERY.glob("*/data.json"))
        for s in json.loads(f.read_text())["slides"]
        for x in s["says"]
        if x.get("text")
    ]


def test_sentences_are_counted_in_spoken_words():
    text = "The parseRequest call runs first. Then cache.get answers in 3.5 ms, e.g. on a hit!"
    h = heard(text, [(4, 16), (39, 48)])  # display text: code ranges, no backticks
    assert (h.sentences, h.longest, h.words) == (2, 12, 18)  # parse request, cache get: two words each; 3.5: one; e.g. ends no sentence


def test_numbers_are_counted_once_however_they_are_said():
    assert heard("It went from ten point two to sixteen, about twenty percent.").numbers == 3
    assert heard("TLS 1.3 sends 1,500 bytes for $5 to 40% of x264 users.").numbers == 4  # x264 is a name
    assert heard("The one that wins takes one hundred and five turns.").numbers == 1  # "one" alone is a pronoun


def test_no_gallery_paragraph_is_flagged():
    for text, code in gallery_paragraphs():
        h = heard(text, code)
        assert h.longest <= LONGEST_SENTENCE, text
        assert h.sentences < 2 or h.mean <= MEAN_SENTENCE, text
        assert h.numbers < NUMBERS_MIN or h.per_100 <= NUMBERS_PER_100, text


def test_measuring_costs_milliseconds():
    paras = gallery_paragraphs()
    t0 = time.perf_counter()
    for text, code in paras:
        heard(text, code)
    assert time.perf_counter() - t0 < 0.05, f"{len(paras)} paragraphs"


def test_a_long_sentence_steers_the_next_slides(build):
    _, b = build(OPENING + f"\n## Load {{#load}}\n\n{LONG}\n\nA short one.\n")
    found = [i for i in b.issues if i.code == "long_sentences"]
    assert len(found) == 1 and found[0].slide == "load" and "39 spoken words" in found[0].message
    assert "next" in found[0].fix and "revise" not in found[0].fix


def test_many_numbers_close_together_are_flagged_once_a_slide(build):
    nums = "It took 340 ms, then 120 ms, then 95 ms with 4 workers and 2 caches."
    _, b = build(OPENING + f"\n## Speed {{#speed}}\n\n{nums}\n\n{nums}\n")
    assert [i.code for i in b.issues if i.code in ("dense_numbers", "long_sentences")] == ["dense_numbers"]


def test_plain_narration_adds_nothing(build):
    _, b = build(OPENING + "\n## Load {#load}\n\nThe loader reads the manifest. It fetches what is missing, then saves it.\n")
    assert not {"long_sentences", "dense_numbers"} & {i.code for i in b.issues}
