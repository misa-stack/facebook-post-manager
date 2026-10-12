"""Authorization, import and token renewal for existing Pages."""

from __future__ import annotations

import sys
from pathlib import Path

from fbmanager import cli
from fbmanager.management import ManagementClient
from fbmanager.storage import Store, validate_name


def add_parsers(commands, accounts) -> None:
    auth = commands.add_parser("auth", help="Set up long-lived User and Page authorization")
    sub = auth.add_subparsers(dest="auth_command", required=True)
    setup = sub.add_parser(
        "setup", help="Exchange User authorization and save encrypted credentials"
    )
    setup.add_argument("--app-id")
    setup.add_argument("--app-secret-file", type=Path)
    setup.add_argument("--user-token-file", type=Path)
    setup.add_argument("--api-version")
    renew = sub.add_parser("renew", help="Exchange fresh User authorization for the same app/User")
    renew.add_argument("--user-token-file", type=Path)
    renew.add_argument("--api-version")
    for name, help_text in (
        ("status", "Check current User authorization (never prints tokens)"),
        ("pages", "List authorized existing Pages (never prints tokens)"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--api-version")
    attach = accounts.add_parser("import", help="Save an existing Page using User authorization")
    attach.add_argument("name", help="Local nickname")
    attach.add_argument("--page-id", help="Existing Page ID; omit to choose interactively")
    attach.add_argument("--info", default="")
    attach.add_argument("--api-version")
    refresh = accounts.add_parser(
        "refresh-token", help="Derive a new Page token from User authorization"
    )
    refresh.add_argument("account")
    refresh.add_argument("--api-version")


def read_credential(path: Path | None, label: str) -> str:
    if path is None:
        return cli.secret(f"{label} (hidden): ").strip()
    return (sys.stdin.read() if str(path) == "-" else path.read_text()).strip()


def execute_auth(store: Store, args) -> dict:
    version = cli.api_version(store, args)
    cli.unlock(store, create=args.auth_command == "setup")
    with store.lock({"id": "authorization"}), ManagementClient(version) as client:
        if args.auth_command in {"setup", "renew"}:
            if args.auth_command == "setup":
                app_id = args.app_id
                if not app_id and sys.stdin.isatty():
                    app_id = input("App ID: ").strip()
                if not app_id:
                    raise ValueError("Supply --app-id in scripts.")
                if str(args.app_secret_file) == str(args.user_token_file) == "-":
                    raise ValueError("Only one credential may be read from stdin.")
                app_secret = read_credential(args.app_secret_file, "App Secret")
                old = None
            else:
                old = store.authorization()
                app_id, app_secret = old["app_id"], old["app_secret"]
            token = read_credential(args.user_token_file, "User access token")
            auth = client.exchange(app_id, app_secret, token)
            if old and auth["user_id"] != old["user_id"]:
                raise ValueError("Renew with the same User. Use auth setup to change identity.")
            store.save_authorization(auth)
            return {
                "status": "authorization_saved",
                **auth["metadata"],
                "note": "Page tokens are retrieved separately. No token is irrevocable.",
            }
        auth = store.authorization()
        metadata = client.check_authorization(auth)
        if args.auth_command == "pages":
            return {"pages": client.managed_pages(auth)}
        return {
            "status": "authorization_valid",
            **metadata,
            "note": "Action permissions are checked by Meta when used.",
        }


def execute_accounts(store: Store, args):
    if args.account_command == "import":
        validate_name(args.name)
        if not args.page_id and (not sys.stdin.isatty() or args.json):
            raise ValueError("Supply --page-id in scripts and with --json.")
        version = cli.api_version(store, args)
        cli.unlock(store)
        auth = store.authorization()
        with ManagementClient(version) as client:
            client.check_authorization(auth)
            page_id = args.page_id
            if not page_id:
                pages = client.managed_pages(auth)
                if not pages:
                    raise ValueError("No authorized Pages. Grant Page access and run 'auth renew'.")
                for number, page in enumerate(pages, 1):
                    print(f"{number}. {page['name']} ({page['id']})")
                choice = input("Select existing Page number (Enter cancels): ").strip()
                if not choice:
                    return {"status": "cancelled"}
                if not choice.isdigit() or not 1 <= int(choice) <= len(pages):
                    raise ValueError("Choose one of the listed Page numbers.")
                page_id = pages[int(choice) - 1]["id"]
            page, info, metadata = client.page_token(auth, page_id)
            account = store.add(args.name, args.info, page.page_id, info["name"], page.token)
            store.save_token_metadata(page.page_id, metadata)
        return {"status": "page_imported", **account, "token_inspection": metadata}
    account = store.account(args.account)
    version = cli.api_version(store, args)
    cli.unlock(store)
    with store.lock(account), ManagementClient(version) as client:
        auth = store.authorization()
        client.check_authorization(auth)
        page, info, metadata = client.page_token(auth, account["page_id"])
        store.edit(account["name"], None, None, token=page.token, page_name=info["name"])
        store.save_token_metadata(page.page_id, metadata)
        return {
            "status": "page_token_saved",
            "account": account["name"],
            "page_id": page.page_id,
            **metadata,
        }
