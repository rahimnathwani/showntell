"""Cue resolution against each kind of visual, auto-correction, and inferred cues."""

from __future__ import annotations

import pytest

CODE = "## C {#c}\n\n```python file=src/cache.py lines=10-24\n```\n\n"
DIFF = "## D {#d}\n\n```python old\na = 1\nb = 2\nc = 3\n```\n```python new\na = 1\nc = 4\n```\n\n"
LIST = "## L {#l}\n\n- **Memory**: fast {#memory}\n- **Disk**: survives restarts\n\n"
TABLE = "## Tab {#t}\n\n| Name | Speed |\n|---|---|\n| mem | fast {#m} |\n| disk | slow |\n\n"
DIAGRAM = "## G {#g}\n\n```mermaid\nflowchart LR\n  req[Request] --> hit{In cache?}\n  hit -->|no| load[Load from database]\n```\n\n"


@pytest.fixture
def cues(build, project):
    """`cues(visual_md, narration)` -> (resolved cues of that narration, builder issues), without
    the checks on the whole slide."""

    def _cues(visual: str, narration: str, frame: str = "landscape"):
        w, b = build("# T\n\nOpening.\n\n" + visual + narration + "\n", root=project, frame=frame, check_last=False)
        return w.slides[-1].says[-1].cues, b.issues

    return _cues


def strip(c: dict) -> dict:
    return {k: v for k, v in c.items() if k not in ("at", "end", "label", "target", "text")}


# -- code ------------------------------------------------------------------------------------


def test_line_cue(cues):
    (c,), issues = cues(CODE, "The class [starts here](#L10).")
    assert strip(c) == {"kind": "lines", "side": "new", "l0": 10, "l1": 10} and not issues
    assert (c["at"], c["end"], c["label"], c["target"]) == (10, 21, "starts here", "L10")


def test_line_range_cue(cues):
    (c,), _ = cues(CODE, "See [these](#L12-14).")
    assert (c["l0"], c["l1"]) == (12, 14)


def test_reversed_line_range_is_ordered(cues):
    (c,), _ = cues(CODE, "See [these](#L14-12).")
    assert (c["l0"], c["l1"]) == (12, 14)


def test_range_partly_past_the_code_is_clamped(cues):
    (c,), _ = cues(CODE, "See [these](#L20-30).")
    assert (c["l0"], c["l1"]) == (20, 24)


def test_quoted_text_on_the_line_narrows_the_cue(cues):
    (c,), issues = cues(CODE, '[The guard](#L12 "if key in self._data") checks.')
    assert (c["l0"], c["text"]) == (12, "if key in self._data") and not issues


def test_quoted_text_on_another_line_moves_the_cue_and_says_so(cues):
    (c,), issues = cues(CODE, '[The guard](#L14 "if key in") checks.')
    assert (c["l0"], c["l1"]) == (12, 12)
    assert any("not on line 14" in i and "line 12" in i for i in issues)


def test_quoted_text_nowhere_points_at_the_whole_line(cues):
    (c,), issues = cues(CODE, '[Nothing](#L12 "no such text") here.')
    assert (c["l0"], c["text"]) == (12, "")
    assert any("does not occur" in i for i in issues)


def test_cue_past_the_code_is_dropped_with_an_issue(cues):
    found, issues = cues(CODE, "Look [down there](#L40).")
    assert found == []
    assert any("points past the code" in i and "10-24" in i for i in issues)


def test_cue_past_the_code_with_quoted_text_is_found(cues):
    (c,), issues = cues(CODE, '[Load it](#L3 "value = self.loader(key)").')
    assert c["l0"] == 18 and any("outside lines 10-24" in i for i in issues)


def test_hash_alone_points_at_the_visual(cues):
    (c,), issues = cues(CODE, "Here is [the code](#).")
    assert strip(c) == {"kind": "visual"} and not issues


def test_hash_with_text_searches_the_visual(cues):
    (c,), _ = cues(CODE, '[The guard](# "if key in") checks.')
    assert c["kind"] == "text" and c["text"] == "if key in"


def test_id_cue_on_code_is_an_issue(cues):
    (c,), issues = cues(CODE, "The [thing](#memory) here.")
    assert c["kind"] == "visual" and any("Point at it with `#`" in i for i in issues)


# -- diffs -----------------------------------------------------------------------------------


def test_old_side_cue(cues):
    (c,), _ = cues(DIFF, "It [was two](#-L2).")
    assert (c["side"], c["l0"]) == ("old", 2)


def test_new_side_cue_with_plus(cues):
    (c,), _ = cues(DIFF, "It [is four](#+L2).")
    assert (c["side"], c["l0"]) == ("new", 2)


def test_plain_line_cue_on_a_diff_is_the_new_side(cues):
    (c,), _ = cues(DIFF, "It [is four](#L2).")
    assert (c["side"], c["l0"]) == ("new", 2)


def test_old_side_cue_past_the_old_code(cues):
    found, issues = cues(DIFF, "It [was](#-L9).")
    assert found == [] and any("old side" in i for i in issues)


def test_unified_diff_cues_use_hunk_numbers(cues):
    vis = "## U\n\n```diff\n@@ -10,2 +30,2 @@\n keep\n-old\n+new\n```\n\n"
    (a, b), issues = cues(vis, "From [old](#-L11) to [new](#L31).")
    assert (a["side"], a["l0"], b["side"], b["l0"]) == ("old", 11, "new", 31) and not issues


# -- lists, tables, diagrams ----------------------------------------------------------------------


def test_list_item_cue_by_id(cues):
    (c,), _ = cues(LIST, "The [memory tier](#memory) is fast.")
    assert strip(c) == {"kind": "item", "id": "memory"}


def test_list_item_cue_by_generated_id(cues):
    (c,), _ = cues(LIST, "The [disk](#disk) survives.")
    assert c["id"] == "disk"


def test_unknown_list_item_lists_the_ids(cues):
    found, issues = cues(LIST, "The [cloud](#cloud) tier.")
    assert found == [] and any("no item `cloud`" in i and "memory" in i for i in issues)


def test_line_cue_on_a_list_is_an_issue(cues):
    (c,), issues = cues(LIST, "See [line](#L3).")
    assert c["kind"] == "visual" and any("not code" in i for i in issues)


def test_table_row_cue_by_id_and_generated_id(cues):
    (a, b), issues = cues(TABLE, "The [memory row](#m) and [disk row](#row-2).")
    assert (strip(a), strip(b)) == ({"kind": "row", "id": "m"}, {"kind": "row", "id": "row-2"}) and not issues


def test_unknown_table_row_is_an_issue(cues):
    found, issues = cues(TABLE, "The [x](#zz) row.")
    assert found == [] and any("no row or column `zz`" in i and "col-1 to col-2" in i for i in issues)


def test_table_column_cue_by_position_and_heading(cues):
    (a, b), issues = cues(TABLE, "The [names](#col-1) and [speeds](#col-speed).")
    assert (strip(a), strip(b)) == ({"kind": "col", "col": 0}, {"kind": "col", "col": 1}) and not issues


def test_table_cell_cue_by_row_and_column(cues):
    (a, b, c), issues = cues(TABLE, "Memory is [fast](#m/speed), disk is [slow](#row-2/col-2), and [its name](#row-2/col-name) is short.")
    assert [strip(x) for x in (a, b, c)] == [
        {"kind": "cell", "id": "m", "col": 1},
        {"kind": "cell", "id": "row-2", "col": 1},
        {"kind": "cell", "id": "row-2", "col": 0},
    ]
    assert not issues


def test_table_cell_outside_the_table_is_an_issue(cues):
    found, issues = cues(TABLE, "The [x](#row-9/col-1), the [y](#row-1/col-3) and the [z](#col-0).")
    assert found == []
    assert any("no row `row-9`" in i for i in issues) and any("no column `col-3`" in i for i in issues)
    assert any("no row or column `col-0`" in i for i in issues)


def test_diagram_node_cue_carries_its_label(cues):
    (c,), _ = cues(DIAGRAM, "The [decision](#hit) comes first.")
    assert c["kind"] == "node" and c["id"] == "hit" and c["text"] == "In cache?"


def test_hash_with_text_on_a_diagram(cues):
    (c,), _ = cues(DIAGRAM, '[The decision](# "In cache?") comes first.')
    assert (c["kind"], c["text"]) == ("text", "In cache?")


def test_unknown_flowchart_node_lists_the_nodes(cues):
    _, issues = cues(DIAGRAM, "The [cache](#cache) node.")
    assert any("no node `cache`" in i and "req" in i for i in issues)


# -- cues written before the visual, and slides without one -------------------------------------


def test_cues_written_before_the_visual_are_resolved_when_it_arrives(build, project):
    w, b = build(
        "# T\n\nA.\n\n## C\n\nThe [guard](#L12) first.\n\n```python file=src/cache.py lines=10-24\n```\n", root=project, check_last=False
    )
    (c,) = w.slides[1].says[0].cues
    assert (c["kind"], c["l0"]) == ("lines", 12) and not b.issues


def test_cue_on_a_slide_that_never_gets_a_visual_is_reported(build):
    _, b = build("# T\n\nA.\n\n## Words\n\nLook at [line twelve](#L12).\n\n## Next\n\nB.\n")
    assert any("no visual" in i for i in b.issues)


# -- inferred cues ---------------------------------------------------------------------------------


def test_code_span_matching_a_line_becomes_an_inferred_cue(cues):
    (c,), _ = cues(CODE, "On a miss it calls `self.loader` and stores the result.")
    assert c["inferred"] is True and (c["kind"], c["l0"], c["text"]) == ("lines", 18, "self.loader")
    assert c["label"] == "self.loader"


def test_identifier_in_plain_words_becomes_an_inferred_cue(build):
    md = "# T\n\nA.\n\n## C\n\n```python\ndef load_value(key):\n    return fetch_from_db(key)\n```\n\nThen fetch_from_db runs.\n"
    w, _ = build(md)
    (c,) = w.slides[1].says[0].cues
    assert c["inferred"] and c["l0"] == 2


def test_list_titles_become_inferred_cues(cues):
    found, _ = cues(LIST, "Memory is quick, but disk persists.")
    assert [(c["id"], c["label"]) for c in found] == [("memory", "Memory"), ("disk", "disk")]
    assert all(c["inferred"] for c in found)


def test_table_first_cells_become_inferred_cues(cues):
    found, _ = cues(TABLE, "The disk tier is slow.")
    assert [(c["kind"], c["id"]) for c in found] == [("row", "row-2")]


def test_diagram_labels_become_inferred_cues(cues):
    found, _ = cues(DIAGRAM, "First the request, then we load from database.")
    assert [(c["id"], c["text"]) for c in found] == [("req", "Request"), ("load", "Load from database")]


def test_explicit_cues_switch_inference_off(cues):
    found, _ = cues(LIST, "Memory is quick, see [disk](#disk).")
    assert [c["id"] for c in found] == ["disk"] and not found[0].get("inferred")


def test_at_most_four_inferred_cues(build):
    items = "\n".join(f"- **Item{i}**: x" for i in range(6))
    w, _ = build("# T\n\nA.\n\n## L\n\n" + items + "\n\n" + " ".join(f"Item{i}" for i in range(6)) + ".\n")
    assert len(w.slides[1].says[0].cues) == 4


def test_no_inference_without_a_visual(build):
    w, _ = build("# T\n\nMemory and disk.\n")
    assert w.slides[0].says[0].cues == []


SEQUENCE = "## S {#s}\n\n```mermaid\nsequenceDiagram\n  participant U as User\n  participant B as Bank\n  U->>B: POST /transfer\n  B-->>U: 200 OK\n```\n\n"


def test_sequence_message_cue_points_at_its_text(cues):
    (a, b), issues = cues(SEQUENCE, "The user [posts the form](#U->>B), and the bank [answers](#B-->>U).")
    assert (a["kind"], a["text"]) == ("text", "POST /transfer") and (b["kind"], b["text"]) == ("text", "200 OK")
    assert not issues


def test_unknown_sequence_message_lists_the_messages(cues):
    _, issues = cues(SEQUENCE, "The bank [never calls](#B->>X).")
    assert [i.code for i in issues] == ["cue_no_target"] and "U->>B" in issues[0]


def test_edge_path_on_a_sequence_diagram_names_the_message_form(cues):
    _, issues = cues(SEQUENCE, "A [round trip](#U->B->U).")
    assert issues[0].code == "cue_wrong_kind" and "sequence diagram" in issues[0] and "#Sender->>Receiver" in issues[0]


def test_row_number_works_when_rows_have_ids(cues):
    (c,), issues = cues(TABLE, "The [first row](#row-1) is memory.")
    assert (c["kind"], c["id"]) == ("row", "m") and not issues


def test_a_row_id_in_any_cell_names_the_row(cues):
    table = "## T {#t}\n\n| Month | North | South |\n|---|---|---|\n| June | Summer {#june} | Winter |\n| December | Winter | Summer |\n\n"
    (c,), issues = cues(table, "In [June](#june) the north has summer.")
    assert (c["kind"], c["id"]) == ("row", "june") and not issues
