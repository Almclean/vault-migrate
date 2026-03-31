# vault-migrate

**Replace your Obsidian/Notion markdown vault with a SQLite graph database — and dramatically reduce the token cost of AI agent workflows.**

---

## The Problem

Markdown-based knowledge tools like [Obsidian](https://obsidian.md) are designed for humans. When Claude (or any LLM agent) needs to retrieve knowledge, the workflow looks like this:

1. `Grep` across hundreds of `.md` files to find relevant notes
2. `Read` multiple files to build context
3. Manually follow wikilinks to find related topics
4. Piece everything together

This is slow, token-heavy, and doesn't scale. The graph that Obsidian renders visually has to be reconstructed by the agent on every query.

The bigger realisation: **if Claude is the primary consumer of the knowledge base, optimise for Claude — not for human readability.**

---

## The Solution

Store your knowledge graph in SQLite. Use Claude + a thin CLI (`vault-db`) to read and write it. Keep Obsidian around as a file viewer if you want — or drop it entirely.

**Before:** Grep hundreds of markdown files → read matching files → follow wikilinks → reconstruct context
**After:** `vault-db search "topic"` → `vault-db connections "node"` → done

Token cost reduction on retrieval-heavy workflows is roughly an order of magnitude.

---

## Architecture

### Schema

```sql
-- nodes: one row per concept, person, company, daily note, etc.
CREATE TABLE nodes (
    id      INTEGER PRIMARY KEY,
    title   TEXT UNIQUE NOT NULL,
    type    TEXT,          -- any string you choose — see Node Types below
    body    TEXT,          -- freeform markdown content
    created TEXT,
    updated TEXT
);

-- edges: directed relationships between nodes
CREATE TABLE edges (
    id      INTEGER PRIMARY KEY,
    from_id INTEGER REFERENCES nodes(id),
    to_id   INTEGER REFERENCES nodes(id),
    rel     TEXT DEFAULT 'mentions',   -- any string: mentions, related, attended, etc.
    weight  REAL DEFAULT 1.0
);

-- tags: many-to-many node tagging
CREATE TABLE tags (
    node_id INTEGER REFERENCES nodes(id),
    tag     TEXT
);
```

### Node Types

Types are **free-form strings** — use whatever makes sense for your knowledge base. The migration script infers types from frontmatter tags and folder paths, but you can define your own taxonomy.

Some examples to get started:

| type | example use |
|------|-------------|
| `person` | individuals — colleagues, contacts, authors |
| `organisation` | companies, teams, institutions |
| `concept` | ideas, topics, technologies, projects |
| `daily` | daily journal notes (YYYY-MM-DD) |
| `resource` | articles, books, videos, papers |
| `place` | locations, regions |
| `moc` | Map of Content — hub/index node |
| `stub` | auto-created from an unresolved wikilink |

The only reserved type is `stub` — used internally for wikilinks that have no backing note.

---

## vault-db CLI

```bash
# if installed via uv
uv run vault-db <command>

# or after uv sync
vault-db <command>
```

| command | description |
|---------|-------------|
| `get <title>` | Fetch a node as JSON; `--body` for text only |
| `search <query>` | Full-text search on title + body, returns ranked JSON |
| `upsert <title>` | Create or update a node (`--type`, `--body`, `--tag`) |
| `link <from> <to>` | Add a directed edge (`--rel` defaults to `mentions`) |
| `connections <title>` | Return inbound + outbound edges as JSON |
| `today` | Get or create today's daily node (YYYY-MM-DD) |

### Examples

```bash
# Search for a topic
vault-db search "machine learning"

# Get the full body of a node
vault-db get "Transformer Architecture" --body

# See what a node connects to
vault-db connections "Python"

# Add a new node
vault-db upsert "Attention Mechanism" \
  --type concept \
  --body "## Core Idea\n- ..." \
  --tag topic --tag ml

# Link two nodes
vault-db link "Attention Mechanism" "Transformer Architecture"

# Get or create today's daily note
vault-db today --body
```

---

## Migration from Obsidian

`main.py` is a one-shot migration script that parses your Obsidian vault and populates the SQLite DB.

### Configure

Set environment variables before running:

```bash
export VAULT_PATH=/path/to/your/vault/Notes
export VAULT_DB=/path/to/vault.db   # optional, defaults to ./vault.db
```

Or edit the defaults at the top of `main.py`.

### Run

```bash
uv run main.py
```

### How it works

Three passes:
1. **Pass 1** — parse every `.md` file, extract YAML frontmatter + body, insert nodes + tags
2. **Pass 2** — extract `[[wikilinks]]` from each file, insert edges; unresolved links become `stub` nodes
3. **Pass 3** — collapse case-variant duplicates (`My Topic` vs `my topic` → one canonical node)

### Type inference

Node types are inferred from frontmatter tags and folder path. Customise `infer_type()` in `main.py` to match your vault's conventions:

```python
def infer_type(meta: dict, path: Path) -> str:
    tags = meta.get("tags") or []
    if "daily" in tags:   return "daily"
    if "people" in tags:  return "person"
    if "moc" in tags:     return "moc"
    if "Daily Notes" in str(path): return "daily"
    return "concept"      # default fallback
```

### What gets migrated
- All `.md` files → nodes
- Frontmatter `tags`, `created`, `date` → preserved
- `[[wikilinks]]` → directed edges
- Wikilink aliases (`[[Title|display]]`) and anchors (`[[Title#heading]]`) handled correctly

### Example output
```
Found 240 notes
Pass 1 complete — nodes inserted
Pass 2 complete — 1842 edges inserted
Pass 3 complete — 3 duplicate nodes collapsed

  stub         143
  concept       89
  person        44
  daily         32
  organisation  21
  moc            5

  Top 5 most-linked nodes:
   189  Python
    76  Machine Learning
    61  My Project
    44  Alice Smith
    38  2025
```

Your Obsidian vault is untouched — keep it as a read-only archive or delete it.

---

## Claude Code Skills / Commands

Once your knowledge lives in SQLite, rewrite your Claude Code slash commands to use `vault-db` instead of `Grep`/`Read`/`Write` against markdown files.

The pattern for every command is the same:

```
search → get → upsert → link
```

Example `/log` command flow:
1. Extract nouns from the log message
2. `vault-db search <noun>` to find existing nodes
3. `vault-db today --body` to get today's daily note
4. `vault-db upsert <date>` to write updated body
5. `vault-db link <date> <noun>` for each mention

Example command skeleton (`.claude/commands/log.md`):

```markdown
Log to today's daily note using vault-db.

1. Extract all nouns from the message
2. Search for each: `vault-db search "<noun>"`
3. Get today's body: `vault-db today --body`
4. Append entry and upsert: `vault-db upsert "<date>" --type daily --body "<updated>"`
5. Add edges: `vault-db link "<date>" "<noun>"` for each mention
```

---

## Why Not Postgres?

Postgres supports everything SQLite does (and more — pgvector, concurrent writes, recursive CTEs). But for a personal knowledge base with a single user:

- SQLite is a single portable file — no infra, no server, no connection strings
- Python `sqlite3` is stdlib — zero extra dependencies beyond `pyyaml`
- LLM query latency dominates — you will never hit a SQLite bottleneck

Use Postgres if you need concurrent multi-agent writes or semantic vector search at scale. SQLite is sufficient for personal use.

### On Vectors

Skipped for now. Structured graph + keyword search covers the vast majority of retrieval use cases. Revisit with [`sqlite-vec`](https://github.com/asg017/sqlite-vec) if "find me things conceptually similar to X" becomes a real workflow.

---

## Setup

```bash
git clone https://github.com/Almclean/vault-migrate
cd vault-migrate

# install dependencies + register vault-db CLI
uv sync

# configure vault path
export VAULT_PATH=/path/to/your/vault

# migrate
uv run main.py

# test
vault-db search "something in your vault"
```

**Requirements:** Python 3.10+, [uv](https://github.com/astral-sh/uv)

---

## Project Structure

```
vault-migrate/
├── main.py          # One-shot Obsidian → SQLite migration
├── vault_db/
│   ├── db.py        # Core CRUD: get, search, upsert, link, connections
│   └── cli.py       # vault-db CLI (argparse)
├── pyproject.toml
└── vault.db         # Your knowledge graph (generated, gitignored)
```

---

## Licence

MIT
