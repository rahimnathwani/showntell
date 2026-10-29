"""The speech queue: one priority queue per user, served round-robin."""

from __future__ import annotations

import time

from showntell.pronounce import Spoken
from showntell.speech.fake import FakeBackend
from showntell.speech.narrator import FairQueue, Narrator


def test_speech_is_served_round_robin_across_users(tmp_path):

    order: list[str] = []

    class Recording(FakeBackend):
        def synth(self, text, voice, speed):
            order.append(text)
            return super().synth(text, voice, speed)

    n = Narrator(Recording(), tmp_path / "audio")
    for i in range(5):
        n.request(f"a{i}", Spoken(f"alice {i}"), "af_heart", 1.0, i, lambda r: None, user="alice")
    for i in range(2):
        n.request(f"b{i}", Spoken(f"bob {i}"), "af_heart", 1.0, i, lambda r: None, user="bob")
    n.request("c0", Spoken("carol 0"), "af_heart", 1.0, 0, lambda r: None, user="carol")
    n.start()
    deadline = time.monotonic() + 5
    while len(order) < 9 and time.monotonic() < deadline:  # "Ready." warms the backend first
        time.sleep(0.01)
    assert order[1:] == ["alice 0", "bob 0", "carol 0", "alice 1", "bob 1", "alice 2", "alice 3", "alice 4"]


def test_one_user_is_a_plain_priority_queue():
    q = FairQueue()
    for item in [(3, 0, "c"), (1, 1, "a"), (2, 2, "b")]:
        q.put("", item)
    assert [q.get_nowait()[2] for _ in range(3)] == ["a", "b", "c"] and q.qsize() == 0
