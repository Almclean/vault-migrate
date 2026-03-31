"""Tests for vault_db.db core CRUD operations."""

import sqlite3
import pytest
from vault_db.db import get_node, search_nodes, upsert_node, add_edge, get_connections


@pytest.fixture
def con():
    """In-memory SQLite DB with schema initialised."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript("""
        CREATE TABLE nodes (
            id      INTEGER PRIMARY KEY,
            title   TEXT UNIQUE NOT NULL,
            type    TEXT,
            body    TEXT,
            created TEXT,
            updated TEXT
        );
        CREATE TABLE edges (
            id      INTEGER PRIMARY KEY,
            from_id INTEGER REFERENCES nodes(id),
            to_id   INTEGER REFERENCES nodes(id),
            rel     TEXT DEFAULT 'mentions',
            weight  REAL DEFAULT 1.0
        );
        CREATE TABLE tags (
            node_id INTEGER REFERENCES nodes(id),
            tag     TEXT
        );
    """)
    return c


# ---------------------------------------------------------------------------
# upsert_node
# ---------------------------------------------------------------------------

def test_upsert_creates_node(con):
    node = upsert_node(con, "Python", type_="concept", body="## Core Idea\n- A language")
    assert node["title"] == "Python"
    assert node["type"] == "concept"


def test_upsert_updates_existing(con):
    upsert_node(con, "Python", type_="concept", body="v1")
    node = upsert_node(con, "Python", type_="concept", body="v2")
    assert node["body"] == "v2"


def test_upsert_does_not_downgrade_to_stub(con):
    upsert_node(con, "Python", type_="concept", body="real")
    node = upsert_node(con, "Python", type_="stub")
    assert node["type"] == "concept"


def test_upsert_stores_tags(con):
    node = upsert_node(con, "Python", type_="concept", tags=["topic", "technical"])
    assert set(node["tags"]) == {"topic", "technical"}


def test_upsert_replaces_tags_on_update(con):
    upsert_node(con, "Python", type_="concept", tags=["old"])
    node = upsert_node(con, "Python", type_="concept", tags=["new"])
    assert node["tags"] == ["new"]


# ---------------------------------------------------------------------------
# get_node
# ---------------------------------------------------------------------------

def test_get_node_found(con):
    upsert_node(con, "Alice", type_="person")
    node = get_node(con, "Alice")
    assert node is not None
    assert node["title"] == "Alice"


def test_get_node_case_insensitive(con):
    upsert_node(con, "Alice", type_="person")
    assert get_node(con, "alice") is not None
    assert get_node(con, "ALICE") is not None


def test_get_node_not_found(con):
    assert get_node(con, "Nobody") is None


# ---------------------------------------------------------------------------
# search_nodes
# ---------------------------------------------------------------------------

def test_search_matches_title(con):
    upsert_node(con, "Machine Learning", type_="concept", body="## Core Idea\n- ML stuff")
    results = search_nodes(con, "Machine")
    assert any(r["title"] == "Machine Learning" for r in results)


def test_search_matches_body(con):
    upsert_node(con, "Note", type_="note", body="mentions transformers")
    results = search_nodes(con, "transformers")
    assert any(r["title"] == "Note" for r in results)


def test_search_title_ranked_before_body(con):
    upsert_node(con, "Python", type_="concept", body="not about python")
    upsert_node(con, "Other", type_="concept", body="python is mentioned here")
    results = search_nodes(con, "python")
    assert results[0]["title"] == "Python"


def test_search_no_results(con):
    assert search_nodes(con, "xyzzy") == []


def test_search_respects_limit(con):
    for i in range(20):
        upsert_node(con, f"Note {i}", type_="note", body="common word")
    results = search_nodes(con, "common", limit=5)
    assert len(results) <= 5


# ---------------------------------------------------------------------------
# add_edge
# ---------------------------------------------------------------------------

def test_add_edge_creates_nodes_if_missing(con):
    add_edge(con, "Alice", "Bob")
    assert get_node(con, "Alice") is not None
    assert get_node(con, "Bob") is not None


def test_add_edge_created_true_for_new(con):
    created = add_edge(con, "Alice", "Bob")
    assert created is True


def test_add_edge_no_duplicate(con):
    add_edge(con, "Alice", "Bob")
    add_edge(con, "Alice", "Bob")
    count = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert count == 1


def test_add_edge_no_self_loop(con):
    result = add_edge(con, "Alice", "Alice")
    assert result is False
    count = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert count == 0


def test_add_edge_custom_rel(con):
    add_edge(con, "Alice", "Bob", rel="manages")
    row = con.execute("SELECT rel FROM edges").fetchone()
    assert row["rel"] == "manages"


# ---------------------------------------------------------------------------
# get_connections
# ---------------------------------------------------------------------------

def test_connections_outbound(con):
    upsert_node(con, "A", type_="concept")
    upsert_node(con, "B", type_="concept")
    add_edge(con, "A", "B")
    result = get_connections(con, "A")
    assert any(n["title"] == "B" for n in result["links_to"])


def test_connections_inbound(con):
    upsert_node(con, "A", type_="concept")
    upsert_node(con, "B", type_="concept")
    add_edge(con, "A", "B")
    result = get_connections(con, "B")
    assert any(n["title"] == "A" for n in result["linked_from"])


def test_connections_not_found(con):
    assert get_connections(con, "Nobody") == {}


def test_connections_no_duplicates(con):
    upsert_node(con, "A", type_="concept")
    upsert_node(con, "B", type_="concept")
    add_edge(con, "A", "B")
    add_edge(con, "A", "B")  # duplicate attempt
    result = get_connections(con, "A")
    titles = [n["title"] for n in result["links_to"]]
    assert titles.count("B") == 1
