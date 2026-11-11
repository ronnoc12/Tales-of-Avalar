"""Command line: ``chronicler validate | review | serve | user``."""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from pathlib import Path

from chronicler.campaign import Campaign
from chronicler.review import ReviewError, review_entry, status_changes
from chronicler.validate import validate

DEFAULT_CAMPAIGN = "campaign"


def _campaign(path: str) -> Campaign | None:
    root = Path(path)
    if not root.is_dir():
        print(f"error: campaign folder not found: {root}", file=sys.stderr)
        return None
    return Campaign(root)


def cmd_validate(args: argparse.Namespace) -> int:
    campaign = _campaign(args.campaign)
    if campaign is None:
        return 2
    issues = validate(campaign)
    shown = [i for i in issues if i.level == "error" or not args.errors_only]
    for issue in shown:
        print(issue.format())
    errors = sum(1 for i in issues if i.level == "error")
    warnings = len(issues) - errors
    entries = len(campaign.gm.index.docs)
    print(f"\n{entries} entries checked: {errors} error(s), {warnings} warning(s).")
    return 1 if errors else 0


def _print_report(campaign: Campaign, entry_id: str) -> None:
    index = campaign.gm.index
    report = review_entry(campaign, entry_id)
    print(f"\n== {report.name} ({report.entry_id}) is now: {report.status}")
    if report.empty:
        print("   Nothing else refers to it.")
        return
    if report.canon_linkers:
        print("   Canon entries still linking to it (update or unlink):")
        for ref in report.canon_linkers:
            print(f"     - {index.docs[ref.source_id].rel_path}:{ref.line or ''} {ref.raw}")
    if report.backlinks:
        print("   Linked from:")
        for ref in report.backlinks:
            where = f":{ref.line}" if ref.line else f" [{ref.field}]"
            print(f"     - {index.docs[ref.source_id].rel_path}{where}")
    if report.mentions:
        print("   Mentioned (unlinked) in:")
        for mention in report.mentions:
            where = f"{index.docs[mention.source_id].rel_path}:{mention.line}"
            print(f"     - {where}  …{mention.snippet}…")
    if report.alternates:
        print("   Alternate versions to reconsider:")
        for other in report.alternates:
            print(f"     - {index.name_of(other)} ({other})")


def cmd_review(args: argparse.Namespace) -> int:
    campaign = _campaign(args.campaign)
    if campaign is None:
        return 2
    index = campaign.gm.index
    if args.entry:
        resolution = index.resolve(args.entry)
        if not resolution.ok or resolution.entry_id is None:
            hint = (
                f" Did you mean: {', '.join(resolution.suggestions)}?"
                if resolution.suggestions
                else ""
            )
            print(f"error: no entry matches {args.entry!r}.{hint}", file=sys.stderr)
            return 2
        _print_report(campaign, resolution.entry_id)
        return 0
    try:
        changes = status_changes(campaign)
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not changes:
        print("No status changes since the last commit.")
        return 0
    for change in changes:
        before = change.old or "new"
        print(f"{change.rel_path}: {before} -> {change.new}")
        if change.entry_id in index.docs:
            _print_report(campaign, change.entry_id)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from chronicler.web.app import create_app

    root = Path(args.campaign)
    if not root.is_dir():
        print(f"error: campaign folder not found: {root}", file=sys.stderr)
        return 2
    print(f"Chronicler: http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(root), host=args.host, port=args.port, log_level="warning")
    return 0


def _ask_password() -> str | None:
    first = getpass.getpass("Password (at least 8 characters): ")
    if getpass.getpass("Password again: ") != first:
        print("error: the two passwords do not match", file=sys.stderr)
        return None
    return first


def cmd_user(args: argparse.Namespace) -> int:
    """Accounts from the terminal: the way back in if the GM password is forgotten."""
    from chronicler.auth import AuthError, AuthStore
    from chronicler.db import Database
    from chronicler.storage import DATA_DIR

    root = Path(args.campaign)
    if not root.is_dir():
        print(f"error: campaign folder not found: {root}", file=sys.stderr)
        return 2
    store = AuthStore(Database(root / DATA_DIR))
    if args.action == "list":
        for user in store.users():
            print(f"{user.username}  ({'GM' if user.gm else 'Player'})")
        return 0
    if not args.name:
        print("error: give the account name", file=sys.stderr)
        return 2
    password = _ask_password()
    if password is None:
        return 1
    try:
        if args.action == "add":
            store.create_user(args.name, password, args.role)
            print(f"Added {args.name} ({args.role}).")
        else:
            store.set_password(args.name, password)
            print(f"Password changed for {args.name}; they are logged out everywhere.")
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chronicler", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def campaign_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("campaign", nargs="?", default=DEFAULT_CAMPAIGN, help="campaign folder")

    p_validate = sub.add_parser("validate", help="check every entry for problems")
    campaign_arg(p_validate)
    p_validate.add_argument("--errors-only", action="store_true", help="hide warnings")
    p_validate.set_defaults(func=cmd_validate)

    p_review = sub.add_parser("review", help="list what to re-check after status changes")
    campaign_arg(p_review)
    p_review.add_argument("--entry", help="review one entry by name, alias, or id")
    p_review.set_defaults(func=cmd_review)

    p_serve = sub.add_parser("serve", help="run the viewer")
    campaign_arg(p_serve)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    p_user = sub.add_parser("user", help="add an account, reset a password, or list accounts")
    p_user.add_argument("action", choices=("add", "reset-password", "list"))
    p_user.add_argument("name", nargs="?", help="account name")
    p_user.add_argument("--role", choices=("gm", "player"), default="player")
    p_user.add_argument("--campaign", default=DEFAULT_CAMPAIGN, help="campaign folder")
    p_user.set_defaults(func=cmd_user)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
