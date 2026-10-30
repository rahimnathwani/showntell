"""How a paragraph of narration reads by ear: sentence length in spoken words, the longest
sentence, and numbers per 100 words. Text arithmetic only, so it can run on every add_slides
call without delaying the voice."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Spoken words: identifiers in `code` are said in parts (parse_request, parseRequest: two words),
# a number written in digits is one word (3.5, 1,500, 40%), and so is a name with digits (x264).
_NUMBER = r"[$€£¥]?\d+(?:[.,:]\d+)*%?"
_TOKEN = re.compile(rf"[A-Za-z]+\d[A-Za-z\d]*|{_NUMBER}|[A-Za-z]+(?:'[A-Za-z]+)?")
# A number heard is a run of number words or digits: "ten point two", "twenty percent", "TLS 1.3"
# each count once. "one" alone is left out, since it is as often a pronoun as a count.
_UNITS = "two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion trillion"
_NUMERAL = set(f"zero {_UNITS} {_TENS}".split())
_JOINS = {"point", "and", "percent"}
_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
# A sentence ends at . ! ? (or …) and a space; a decimal point or a dotted name has no space after it.
_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\betc)[.!?…]+[\"'”’)\]]*\s+")


@dataclass
class Heard:
    words: int
    sentences: int
    longest: int  # spoken words in the longest sentence
    numbers: int

    @property
    def mean(self) -> float:
        return self.words / self.sentences if self.sentences else 0.0

    @property
    def per_100(self) -> float:
        return 100 * self.numbers / self.words if self.words else 0.0


def _spoken(text: str, code: list) -> str:
    """The display text with each `code` range replaced by the words the voice says for it."""
    out, last = [], 0
    for a, b in sorted(code):
        out.append(text[last:a])
        out.append(" ".join(_PARTS.findall(text[a:b])))
        last = b
    out.append(text[last:])
    return "".join(out)


def heard(text: str, code: list | None = None) -> Heard:
    spoken = _spoken(text, code or [])
    lengths, numbers = [], 0
    for sentence in _END.split(spoken):
        tokens = _TOKEN.findall(sentence)
        if tokens:
            lengths.append(len(tokens))
            numbers += _count_numbers(tokens)
    return Heard(sum(lengths), len(lengths), max(lengths, default=0), numbers)


def _count_numbers(tokens: list[str]) -> int:
    low = [t.lower() for t in tokens]
    num = [not t[0].isalpha() or w in _NUMERAL for t, w in zip(tokens, low, strict=True)]
    for i, w in enumerate(low):  # "one hundred", "one point five", "twenty one"
        if w == "one" and ((i and num[i - 1]) or (i + 1 < len(low) and (num[i + 1] or low[i + 1] == "point"))):
            num[i] = True
    count, run = 0, False
    for i, w in enumerate(low):
        if num[i]:
            count += not run
            run = True
        elif not (run and w in _JOINS):
            run = False
    return count
