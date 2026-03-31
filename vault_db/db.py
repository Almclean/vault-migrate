import sqlite3
import os
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("VAULT_DB", "vault.db"))


def connect() -> sqlite3.Connection:
    path = os.environ.get("VAULT_DB", str(DEFAULT_DB))
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def get_node(con: sqlite3.Connection, title: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM nodes WHERE LOWER(title) = LOWER(?)", (title,)
    ).fetchone()
    if not row:
        return None
    node = dict(row)
    node["tags"] = [
        r["tag"] for r in con.execute(
            "SELECT tag FROM tags WHERE node_id=?", (node["id"],)
        )
    ]
    return node


def search_nodes(con: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    rows = con.execute("""
        SELECT id, title, type, created,
               SUBSTR(body, 1, 200) AS snippet
        FROM nodes
        WHERE LOWER(title) LIKE LOWER(?) OR LOWER(body) LIKE LOWER(?)
        ORDER BY
            CASE WHEN LOWER(title) LIKE LOWER(?) THEN 0 ELSE 1 END,
            type != 'stub'
        LIMIT ?
    """, (f"%{query}%", f"%{query}%", f"%{query}%", limit)).fetchall()
    return [dict(r) for r in rows]


def upsert_node(
    con: sqlite3.Connection,
    title: str,
    type_: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    created: str | None = None,
) -> dict | None:
    existing = con.execute(
        "SELECT id, type FROM nodes WHERE LOWER(title) = LOWER(?)", (title,)
    ).fetchone()

    if existing:
        node_id = existing["id"]
        # don't downgrade a real node to stub
        effective_type = type_ if (type_ and type_ != "stub") else existing["type"]
        con.execute("""
            UPDATE nodes SET type=?, body=COALESCE(?, body), updated=DATE('now')
            WHERE id=?
        """, (effective_type, body, node_id))
    else:
        con.execute("""
            INSERT INTO nodes (title, type, body, created)
            VALUES (?, ?, ?, COALESCE(?, DATE('now')))
        """, (title, type_ or "concept", body, created))
        node_id = con.execute(
            "SELECT id FROM nodes WHERE LOWER(title) = LOWER(?)", (title,)
        ).fetchone()["id"]

    if tags:
        con.execute("DELETE FROM tags WHERE node_id=?", (node_id,))
        for tag in tags:
            con.execute(
                "INSERT INTO tags (node_id, tag) VALUES (?, ?)", (node_id, tag)
            )

    con.commit()
    return get_node(con, title)


def add_edge(
    con: sqlite3.Connection,
    from_title: str,
    to_title: str,
    rel: str = "mentions",
) -> bool:
    # ensure both nodes exist
    for title in (from_title, to_title):
        if not con.execute(
            "SELECT 1 FROM nodes WHERE LOWER(title) = LOWER(?)", (title,)
        ).fetchone():
            con.execute(
                "INSERT INTO nodes (title, type) VALUES (?, 'stub')", (title,)
            )

    from_id = con.execute(
        "SELECT id FROM nodes WHERE LOWER(title) = LOWER(?)", (from_title,)
    ).fetchone()["id"]
    to_id = con.execute(
        "SELECT id FROM nodes WHERE LOWER(title) = LOWER(?)", (to_title,)
    ).fetchone()["id"]

    if from_id == to_id:
        return False

    existing = con.execute(
        "SELECT 1 FROM edges WHERE from_id=? AND to_id=? AND rel=?",
        (from_id, to_id, rel)
    ).fetchone()
    if not existing:
        con.execute(
            "INSERT INTO edges (from_id, to_id, rel) VALUES (?, ?, ?)",
            (from_id, to_id, rel)
        )
    con.commit()
    return True


def get_connections(
    con: sqlite3.Connection,
    title: str,
    depth: int = 1,  # reserved for future multi-hop traversal
) -> dict:
    root = con.execute(
        "SELECT id, title, type FROM nodes WHERE LOWER(title) = LOWER(?)", (title,)
    ).fetchone()
    if not root:
        return {}

    result: dict = {
        "title": root["title"],
        "type": root["type"],
        "links_to": [],
        "linked_from": [],
    }

    # outbound
    for row in con.execute("""
        SELECT DISTINCT n.title, n.type, e.rel
        FROM edges e JOIN nodes n ON e.to_id = n.id
        WHERE e.from_id = ?
        ORDER BY n.type, n.title
    """, (root["id"],)):
        result["links_to"].append({"title": row["title"], "type": row["type"], "rel": row["rel"]})

    # inbound
    for row in con.execute("""
        SELECT DISTINCT n.title, n.type, e.rel
        FROM edges e JOIN nodes n ON e.from_id = n.id
        WHERE e.to_id = ?
        ORDER BY n.type, n.title
    """, (root["id"],)):
        result["linked_from"].append({"title": row["title"], "type": row["type"], "rel": row["rel"]})

    return result
