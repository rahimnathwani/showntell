"""Mermaid node parsing for flowcharts, sequence diagrams and state diagrams."""

from __future__ import annotations

import pytest

from showntell.mermaid import parse_mermaid

DOC_FLOWCHART = """flowchart LR
  req[Request] --> hit{In cache?}
  hit -->|yes| ret[Return value]
  hit -->|no| load[Load from database]
"""


def test_flowchart_nodes_labels_and_order():
    nodes, labels, order = parse_mermaid(DOC_FLOWCHART)
    assert nodes == {"req": "Request", "hit": "In cache?", "ret": "Return value", "load": "Load from database"}
    assert order == ["req", "hit", "ret", "load"]
    assert labels == ["yes", "no"]


def test_graph_keyword_and_bare_ids_with_spaces():
    nodes, _, order = parse_mermaid("graph TD\n  A --> B\n  B --> C\n")
    assert order == ["A", "B", "C"] and nodes == {"A": "A", "B": "B", "C": "C"}


@pytest.mark.parametrize(
    "line,node,label",
    [
        ('a["Quoted label"] --> b', "a", "Quoted label"),
        ("b((Circle)) --> c", "b", "Circle"),
        ("c[(Database)] --> d", "c", "Database"),
        ("d{{Hexagon}} --> e", "d", "Hexagon"),
        ("e([Stadium]) --> f", "e", "Stadium"),
        ("f[[Subroutine]] --> g", "f", "Subroutine"),
        ("g>Flag] --> h", "g", "Flag"),
    ],
)
def test_flowchart_node_shapes(line, node, label):
    nodes, _, _ = parse_mermaid("flowchart LR\n  " + line + "\n")
    assert nodes[node] == label


def test_first_label_wins_and_later_bare_mentions_keep_it():
    nodes, _, order = parse_mermaid("flowchart LR\n  a[Alpha] --> b\n  b --> a\n")
    assert nodes == {"a": "Alpha", "b": "b"} and order == ["a", "b"]


def test_ampersand_groups():
    nodes, _, _ = parse_mermaid("flowchart LR\n  A & B --> C\n")
    assert set(nodes) == {"A", "B", "C"}


def test_subgraph_title_is_a_label_not_a_node():
    nodes, labels, _ = parse_mermaid("flowchart TB\n  subgraph agent [Coding agent]\n    s[Start] --> o[Open]\n  end\n")
    assert set(nodes) == {"s", "o"} and "Coding agent" in labels


def test_comments_and_styles_are_ignored():
    nodes, _, _ = parse_mermaid("flowchart LR\n  %% a comment\n  a[A] --> b[B]\n  classDef hot fill:#f00\n  style a fill:#0f0\n")
    assert set(nodes) == {"a", "b"}


def test_flowchart_edges_without_spaces():
    nodes, _, order = parse_mermaid("graph TD\n  A-->B\n  B-->C[Cee]\n")
    assert nodes == {"A": "A", "B": "B", "C": "Cee"} and order == ["A", "B", "C"]


def test_flowchart_edge_text_is_a_label_not_a_node():
    nodes, labels, _ = parse_mermaid("flowchart LR\n  a[A] -- calls --> b[B]\n")
    assert set(nodes) == {"a", "b"} and labels == ["calls"]


def test_sequence_diagram_participants_and_messages():
    src = "sequenceDiagram\n  participant A as Alice\n  actor B\n  A ->> B: hello there\n  B -->> C: reply\n"
    nodes, labels, order = parse_mermaid(src)
    assert nodes == {"A": "Alice", "B": "B", "C": "C"}
    assert order == ["A", "B", "C"] and labels == ["hello there", "reply"]


def test_sequence_messages_without_spaces():
    nodes, _, _ = parse_mermaid("sequenceDiagram\n  participant A as Alice\n  A->>B: hi\n  B-->>A: ok\n")
    assert nodes == {"A": "Alice", "B": "B"}


def test_state_diagram():
    src = 'stateDiagram-v2\n  [*] --> Idle\n  state "Busy working" as Busy\n  Idle --> Busy : start\n  Busy --> [*]\n'
    nodes, labels, order = parse_mermaid(src)
    assert nodes == {"Idle": "Idle", "Busy": "Busy working"}
    assert order == ["Idle", "Busy"] and labels == ["start"]


def test_other_diagram_types_collect_quoted_labels():
    nodes, labels, _ = parse_mermaid('pie title Pets\n  "Dogs" : 386\n  "Cats" : 85\n')
    assert nodes == {} and labels == ["Dogs", "Cats"]


def test_diagram_visual_records_type_nodes_and_alt(build):
    w, _ = build('# T\n\nA.\n\n## G {alt="Cache flow"}\n\n```mermaid\n' + DOC_FLOWCHART + "```\n\nWords.\n")
    v = w.slides[1].visual
    assert w.slides[1].kind == "diagram" and v["type"] == "flowchart" and v["alt"] == "Cache flow"
    assert v["order"] == ["req", "hit", "ret", "load"] and v["source"].startswith("flowchart LR")


def test_frontmatter_and_flowchart_elk_keep_nodes_and_ask_for_elk():
    from showntell.mermaid import asks_elk, diagram_type

    src = "---\ntitle: Flow\nconfig:\n  layout: elk\n---\nflowchart LR\n  a[A] --> b[B]\n"
    assert parse_mermaid(src)[0] == {"a": "A", "b": "B"} and diagram_type(src) == "flowchart" and asks_elk(src)
    assert parse_mermaid("flowchart-elk TB\n  a --> b")[0] == {"a": "a", "b": "b"} and asks_elk("flowchart-elk TB\n  a --> b")
    assert not asks_elk('flowchart LR\n  a["layout: elk"] --> b')  # a label is not config
    assert asks_elk('%%{init: {"layout": "elk"}}%%\nflowchart LR\n  a --> b')
