import os
import re
import sqlite3
import yaml
from pathlib import Path

# Configure via environment variables or edit these defaults
VAULT = Path(os.environ.get("VAULT_PATH", "~/vault/Notes")).expanduser()
DB_PATH = Path(os.environ.get("VAULT_DB", "vault.db"))
WIKILINK = re.compile(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]')


def parse_file(path: Path) -> tuple[dict, str, list[str]]:
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            _, fm, body = parts
            try:
                meta = yaml.safe_load(fm) or {}
            except yaml.YAMLError:
                meta = {}
        else:
            meta, body = {}, raw
    else:
        meta, body = {}, raw
    links = WIKILINK.findall(body)
    return meta, body.strip(), links


def infer_type(meta: dict, path: Path) -> str:
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if "daily" in tags:
        return "daily"
    if "moc" in tags:
        return "moc"
    if "people" in tags:
        return "person"
    if "business" in tags:
        return "account"
    if "Daily Notes" in str(path):
        return "daily"
    if "MOCs" in str(path):
        return "moc"
    return "concept"


def init_db(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
            id      INTEGER PRIMARY KEY,
            title   TEXT UNIQUE NOT NULL,
            type    TEXT,
            body    TEXT,
            created TEXT,
            updated TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            id      INTEGER PRIMARY KEY,
            from_id INTEGER REFERENCES nodes(id),
            to_id   INTEGER REFERENCES nodes(id),
            rel     TEXT DEFAULT 'mentions',
            weight  REAL DEFAULT 1.0
        );
        CREATE TABLE IF NOT EXISTS tags (
            node_id INTEGER REFERENCES nodes(id),
            tag     TEXT
        );
    """)


def upsert_node(con: sqlite3.Connection, title: str, type_: str, body: str, meta: dict) -> int:
    created = meta.get("created") or meta.get("date")
    con.execute("""
        INSERT INTO nodes (title, type, body, created)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(title) DO UPDATE SET
            type    = CASE WHEN excluded.type != 'stub' THEN excluded.type ELSE type END,
            body    = excluded.body,
            updated = DATE('now')
    """, (title, type_, body, str(created) if created else None))
    return con.execute("SELECT id FROM nodes WHERE title=?", (title,)).fetchone()[0]


def get_or_create_stub(con: sqlite3.Connection, title: str) -> int:
    row = con.execute("SELECT id FROM nodes WHERE title=?", (title,)).fetchone()
    if row:
        return row[0]
    con.execute("INSERT INTO nodes (title, type) VALUES (?, 'stub')", (title,))
    return con.execute("SELECT id FROM nodes WHERE title=?", (title,)).fetchone()[0]


def migrate() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing {DB_PATH}")

    con = sqlite3.connect(DB_PATH)
    init_db(con)

    files = list(VAULT.rglob("*.md"))
    print(f"Found {len(files)} notes")

    # Pass 1 — nodes + tags
    node_links: dict[str, list[str]] = {}
    for f in files:
        meta, body, links = parse_file(f)
        title = f.stem
        type_ = infer_type(meta, f)
        node_id = upsert_node(con, title, type_, body, meta)
        node_links[title] = links

        raw_tags = meta.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        for tag in raw_tags:
            con.execute(
                "INSERT INTO tags (node_id, tag) VALUES (?, ?)",
                (node_id, str(tag))
            )

    con.commit()
    print("Pass 1 complete — nodes inserted")

    # Pass 2 — edges
    edge_count = 0
    for title, links in node_links.items():
        row = con.execute("SELECT id FROM nodes WHERE title=?", (title,)).fetchone()
        if not row:
            continue
        from_id = row[0]
        for link in links:
            to_id = get_or_create_stub(con, link)
            if from_id != to_id:
                con.execute(
                    "INSERT INTO edges (from_id, to_id) VALUES (?, ?)",
                    (from_id, to_id)
                )
                edge_count += 1

    con.commit()
    print(f"Pass 2 complete — {edge_count} edges inserted")

    # Pass 3 — dedup case variants
    dedup_count = _dedup_case_variants(con)
    print(f"Pass 3 complete — {dedup_count} duplicate nodes collapsed")

    con.commit()
    con.close()
    print(f"\nDone. DB at {DB_PATH.resolve()}")
    print("\nQuick stats:")
    _print_stats()


def _dedup_case_variants(con: sqlite3.Connection) -> int:
    # Find groups of nodes that differ only by case
    dupes = con.execute("""
        SELECT LOWER(title), GROUP_CONCAT(id || ':' || title || ':' || type, '|')
        FROM nodes
        GROUP BY LOWER(title)
        HAVING COUNT(*) > 1
    """).fetchall()

    collapsed = 0
    for _, group in dupes:
        entries = [e.split(":", 2) for e in group.split("|")]
        # canonical = first non-stub; fallback to first entry
        canonical = next(
            (e for e in entries if e[2] != "stub"),
            entries[0]
        )
        canon_id = int(canonical[0])
        dupes_ids = [int(e[0]) for e in entries if int(e[0]) != canon_id]

        for dupe_id in dupes_ids:
            # repoint all edges that point TO the dupe → point to canonical
            con.execute(
                "UPDATE edges SET to_id=? WHERE to_id=?", (canon_id, dupe_id)
            )
            # repoint all edges that go FROM the dupe → go from canonical
            con.execute(
                "UPDATE edges SET from_id=? WHERE from_id=?", (canon_id, dupe_id)
            )
            # remove self-loops created by the repointing
            con.execute(
                "DELETE FROM edges WHERE from_id = to_id"
            )
            # repoint tags
            con.execute(
                "UPDATE tags SET node_id=? WHERE node_id=?", (canon_id, dupe_id)
            )
            # delete the dupe node
            con.execute("DELETE FROM nodes WHERE id=?", (dupe_id,))
            collapsed += 1

    return collapsed


def _print_stats() -> None:
    con = sqlite3.connect(DB_PATH)
    for row in con.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type ORDER BY 2 DESC"):
        print(f"  {row[0] or 'null':12} {row[1]}")
    stubs = con.execute("SELECT COUNT(*) FROM nodes WHERE type='stub'").fetchone()[0]
    print(f"\n  {stubs} stub nodes (wikilinks with no backing note)")
    top = con.execute("""
        SELECT n.title, COUNT(*) as degree
        FROM edges e JOIN nodes n ON e.to_id = n.id
        GROUP BY n.title ORDER BY degree DESC LIMIT 5
    """).fetchall()
    print("\n  Top 5 most-linked nodes:")
    for title, degree in top:
        print(f"    {degree:4}  {title}")
    con.close()


if __name__ == "__main__":
    migrate()
