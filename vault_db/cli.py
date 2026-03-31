import argparse
import json
import sys

from vault_db.db import connect, get_node, search_nodes, upsert_node, add_edge, get_connections


def cmd_get(args: argparse.Namespace) -> None:
    con = connect()
    node = get_node(con, args.title)
    if not node:
        print(json.dumps({"error": f"Node not found: {args.title}"}), file=sys.stderr)
        sys.exit(1)
    if args.body:
        print(node.get("body") or "")
    else:
        node.pop("body", None)
        print(json.dumps(node, indent=2))


def cmd_search(args: argparse.Namespace) -> None:
    con = connect()
    results = search_nodes(con, args.query, limit=args.limit)
    print(json.dumps(results, indent=2))


def cmd_upsert(args: argparse.Namespace) -> None:
    con = connect()
    node = upsert_node(
        con,
        title=args.title,
        type_=args.type,
        body=args.body,
        tags=args.tag or [],
        created=args.created,
    )
    print(json.dumps(node, indent=2))


def cmd_link(args: argparse.Namespace) -> None:
    con = connect()
    created = add_edge(con, args.from_node, args.to_node, rel=args.rel)
    print(json.dumps({"created": created, "from": args.from_node, "to": args.to_node, "rel": args.rel}))


def cmd_connections(args: argparse.Namespace) -> None:
    con = connect()
    result = get_connections(con, args.title, depth=args.depth)
    if not result:
        print(json.dumps({"error": f"Node not found: {args.title}"}), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2))


def cmd_today(args: argparse.Namespace) -> None:
    """Get or create today's daily note node."""
    from datetime import date
    con = connect()
    today = date.today().isoformat()
    node = get_node(con, today)
    if not node:
        node = upsert_node(con, title=today, type_="daily", tags=["daily"], created=today)
    if args.body:
        print(node.get("body") or "")
    else:
        node.pop("body", None)
        print(json.dumps(node, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="vault-db", description="SQLite vault CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # get
    p = sub.add_parser("get", help="Fetch a node by title")
    p.add_argument("title")
    p.add_argument("--body", action="store_true", help="Print body text only")
    p.set_defaults(func=cmd_get)

    # search
    p = sub.add_parser("search", help="Search nodes by title or body")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    # upsert
    p = sub.add_parser("upsert", help="Create or update a node")
    p.add_argument("title")
    p.add_argument("--type", dest="type", default=None)
    p.add_argument("--body", default=None)
    p.add_argument("--tag", action="append", help="Add tag (repeat for multiple)")
    p.add_argument("--created", default=None)
    p.set_defaults(func=cmd_upsert)

    # link
    p = sub.add_parser("link", help="Add an edge between two nodes")
    p.add_argument("from_node", metavar="from")
    p.add_argument("to_node", metavar="to")
    p.add_argument("--rel", default="mentions")
    p.set_defaults(func=cmd_link)

    # connections
    p = sub.add_parser("connections", help="Get connected nodes")
    p.add_argument("title")
    p.add_argument("--depth", type=int, default=1)
    p.set_defaults(func=cmd_connections)

    # today
    p = sub.add_parser("today", help="Get today's daily note node")
    p.add_argument("--body", action="store_true")
    p.set_defaults(func=cmd_today)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
