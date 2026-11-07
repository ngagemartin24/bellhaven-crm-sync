"""bellhaven-sync: run the daily pipeline, serve the review app, or flag a card for a closer look."""

import argparse
import ipaddress
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from . import rules
from .config import TOKEN_VAR, Config, load_config
from .crm import CrmClient
from .ledger import Ledger
from .pipeline import RunAborted, RunSummary, run_once
from .scrape import http_fetcher

PRIVATE_NETS = [
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
]


class MissingKey(Exception):
    pass


def _today() -> date:
    return datetime.now(UTC).date()


def _dependencies(cfg: Config):
    """The one seam tests replace: the website reader, a read-only CRM client and the decision ledger."""
    if not cfg.token:
        raise MissingKey(f"Set {TOKEN_VAR} (or put it in a git-ignored .env file) before running.")
    return http_fetcher(cfg.site_url), CrmClient(cfg.crm_url, cfg.token, read_only=True), Ledger(cfg.db_path)


def host_allowed(host: str) -> bool:
    """The review app writes to the CRM, so it listens only on this machine or a private network."""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or any(ip.version == n.version and ip in n for n in PRIVATE_NETS)


def _print_summary(s: RunSummary) -> None:
    stated = "?" if s.stated is None else s.stated
    state = "complete" if s.complete else f"incomplete ({s.reason})"
    queue = sum(s.by_section.values())
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    print(
        f"Run {when} · {state} · {s.found} of {stated} locations on the website · "
        f"{s.accounts_read} CRM accounts read · {queue} proposals"
    )
    c = s.counts
    print(
        f"This run: {c.get('new', 0)} new, {c.get('refreshed', 0)} unchanged, "
        f"{c.get('superseded', 0)} replaced, {c.get('blocked', 0)} held"
    )
    if queue:
        parts = [f"{name} {s.by_section[name]}" for name in rules.SECTIONS if s.by_section.get(name)]
        print("Queue: " + " · ".join(parts))
    if not s.complete:
        print("The website read was incomplete, so 'gone from the website' proposals are held.")
    print(f"\nLocations ({len(s.verdicts)})")
    width = max((len(v.subject) for v in s.verdicts), default=0)
    for verdict in rules.VERDICTS:
        rows = [v for v in s.verdicts if v.verdict == verdict]
        if not rows:
            continue
        print(f"  {verdict} ({len(rows)})")
        for v in sorted(rows, key=lambda v: v.subject):
            ids = ", ".join(a[-6:] for a in v.account_ids) or "-"
            band = v.band or "-"
            print(f"    {v.subject:<{width}}  {band:<6}  {ids:<22}  {v.name}")
    print("\nReview: uv run bellhaven-sync serve, then open http://127.0.0.1:8000")


def _run(cfg: Config) -> int:
    try:
        fetch, crm, ledger = _dependencies(cfg)
    except MissingKey as e:
        print(e, file=sys.stderr)
        return 2
    with ledger:
        try:
            summary = run_once(fetch, crm, ledger, cfg.operator_parent_name, _today(), cfg.site_url)
        except RunAborted as e:
            print(f"Run stopped, nothing recorded: {e}", file=sys.stderr)
            return 1
    _print_summary(summary)
    return 0


def _serve(cfg: Config, host: str, port: int) -> int:
    import uvicorn

    from .app import AppLockedError, create_app

    if not cfg.token:
        print(f"Set {TOKEN_VAR} before serving: approvals write to the CRM.", file=sys.stderr)
        return 2
    ledger = Ledger(cfg.db_path)
    try:
        app = create_app(cfg, ledger, lambda: CrmClient(cfg.crm_url, cfg.token, read_only=False), _today)
    except AppLockedError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"Review app: http://{host}:{port}  (Ctrl-C to stop)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _flag(cfg: Config, items: list[tuple[str, str]]) -> int:
    with Ledger(cfg.db_path) as ledger:
        for card_id, reason in items:
            try:
                ledger.card(card_id)
            except KeyError:
                print(f"No card {card_id} in {cfg.db_path}", file=sys.stderr)
                return 1
        for card_id, reason in items:
            ledger.flag(card_id, reason)
    print(f"Flagged {len(items)} card(s).")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bellhaven-sync", description=__doc__)
    p.add_argument("--config", default="config.toml", help="settings file (default: config.toml)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="read the website and the CRM, record proposals (never writes to the CRM)")
    s = sub.add_parser("serve", help="the review app; only an approval writes to the CRM")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    f = sub.add_parser("flag", help="mark a card for a closer look (shown on the card)")
    f.add_argument("card_id", nargs="?")
    f.add_argument("reason", nargs="?")
    f.add_argument("--from-json", type=Path, help='a file of [{"card_id": ..., "reason": ...}]')
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve" and not host_allowed(args.host):
        print(
            f"Refusing --host {args.host}: the review app listens only on a loopback or private address "
            "(127.0.0.1, 10/8, 172.16/12, 192.168/16, 100.64/10).",
            file=sys.stderr,
        )
        return 2
    cfg = load_config(Path(args.config))
    if args.command == "run":
        return _run(cfg)
    if args.command == "serve":
        return _serve(cfg, args.host, args.port)
    if args.from_json:
        items = [(x["card_id"], x["reason"]) for x in json.loads(args.from_json.read_text())]
    elif args.card_id and args.reason:
        items = [(args.card_id, args.reason)]
    else:
        print("flag needs <card-id> <reason> or --from-json <file>", file=sys.stderr)
        return 2
    return _flag(cfg, items)


if __name__ == "__main__":
    raise SystemExit(main())
