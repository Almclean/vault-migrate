"""Tests for main.py — infer_type, parse_file, and migration passes."""

import sqlite3
import textwrap
from pathlib import Path

import pytest

from main import infer_type, parse_file


# ---------------------------------------------------------------------------
# infer_type
# ---------------------------------------------------------------------------

def test_infer_type_uses_first_tag():
    assert infer_type({"tags": ["person", "topic"]}) == "person"


def test_infer_type_single_string_tag():
    assert infer_type({"tags": "concept"}) == "concept"


def test_infer_type_no_tags_returns_note():
    assert infer_type({}) == "note"


def test_infer_type_empty_tags_returns_note():
    assert infer_type({"tags": []}) == "note"


# ---------------------------------------------------------------------------
# parse_file
# ---------------------------------------------------------------------------

def test_parse_file_with_frontmatter(tmp_path):
    f = tmp_path / "note.md"
    f.write_text(textwrap.dedent("""\
        ---
        tags: [concept]
        created: "2025-01-01"
        ---
        ## Body
        Links to [[Python]] and [[SQLite]].
    """))
    meta, body, links = parse_file(f)
    assert meta["tags"] == ["concept"]
    assert "## Body" in body
    assert "Python" in links
    assert "SQLite" in links


def test_parse_file_without_frontmatter(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("No frontmatter here. Links to [[Foo]].")
    meta, body, links = parse_file(f)
    assert meta == {}
    assert "Foo" in links


def test_parse_file_wikilink_alias_stripped(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("See [[Real Title|display text]] for details.")
    _, _, links = parse_file(f)
    assert "Real Title" in links
    assert "display text" not in links


def test_parse_file_wikilink_anchor_stripped(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("See [[Note#heading]] for details.")
    _, _, links = parse_file(f)
    assert "Note" in links
    assert "Note#heading" not in links


def test_parse_file_malformed_frontmatter(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("---\n: bad: yaml: [\n---\nBody text.")
    meta, body, _ = parse_file(f)
    assert meta == {}
    assert "Body text" in body


# ---------------------------------------------------------------------------
# full migration (integration)
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Minimal vault with two linked notes."""
    (tmp_path / "Alpha.md").write_text(textwrap.dedent("""\
        ---
        tags: [concept]
        created: "2025-01-01"
        ---
        ## Core Idea
        Alpha links to [[Beta]] and [[Gamma]].
    """))
    (tmp_path / "Beta.md").write_text(textwrap.dedent("""\
        ---
        tags: [person]
        ---
        ## Core Idea
        Beta links back to [[Alpha]].
    """))
    return tmp_path


def _run_migration(vault_path: Path, db_path: Path):
    """Run migration against a temp vault + DB."""
    import main as m
    original_vault = m.VAULT
    original_db = m.DB_PATH
    m.VAULT = vault_path
    m.DB_PATH = db_path
    try:
        m.migrate()
    finally:
        m.VAULT = original_vault
        m.DB_PATH = original_db


def test_migration_creates_nodes(vault, tmp_path):
    db = tmp_path / "test.db"
    _run_migration(vault, db)
    con = sqlite3.connect(db)
    titles = {r[0] for r in con.execute("SELECT title FROM nodes WHERE type != 'stub'")}
    assert "Alpha" in titles
    assert "Beta" in titles


def test_migration_creates_stub_for_unresolved_link(vault, tmp_path):
    db = tmp_path / "test.db"
    _run_migration(vault, db)
    con = sqlite3.connect(db)
    row = con.execute("SELECT type FROM nodes WHERE title='Gamma'").fetchone()
    assert row is not None
    assert row[0] == "stub"


def test_migration_infers_type_from_tags(vault, tmp_path):
    db = tmp_path / "test.db"
    _run_migration(vault, db)
    con = sqlite3.connect(db)
    row = con.execute("SELECT type FROM nodes WHERE title='Beta'").fetchone()
    assert row[0] == "person"


def test_migration_creates_edges(vault, tmp_path):
    db = tmp_path / "test.db"
    _run_migration(vault, db)
    con = sqlite3.connect(db)
    count = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert count > 0


def test_migration_deduplicates_case_variants(tmp_path):
    (tmp_path / "Note.md").write_text("Links to [[python]] and [[Python]].")
    (tmp_path / "python.md").write_text("lowercase python note.")
    db = tmp_path / "test.db"
    _run_migration(tmp_path, db)
    con = sqlite3.connect(db)
    count = con.execute(
        "SELECT COUNT(*) FROM nodes WHERE LOWER(title)='python'"
    ).fetchone()[0]
    assert count == 1
