"""Facebook Page commands and an interactive four-option menu."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
import sys
import warnings
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.table import Table
from rich.text import Text

from fbmanager.graph import GraphClient, GraphError, Page, post_url, valid_id, valid_version
from fbmanager.storage import Store, validate_name


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Manage Facebook Pages with Page access tokens")
    root.add_argument("--json", action="store_true", help="Machine-readable output")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("menu", help="Open the four-option interactive menu")
    configure = commands.add_parser("configure", help="Set or display the Graph API version")
    configure.add_argument("--api-version")
    accounts = commands.add_parser("accounts", help="Add, list, or edit saved Pages")
    sub = accounts.add_subparsers(dest="account_command", required=True)
    from fbmanager.setup_cli import add_parsers

    add_parsers(commands, sub)
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--page-id", required=True)
    add.add_argument("--info", default="")
    add.add_argument("--token-file", type=Path, help="Read token from a file or '-' for stdin")
    add.add_argument("--api-version")
    sub.add_parser("list")
    edit = sub.add_parser("edit")
    edit.add_argument("account")
    edit.add_argument("--name")
    edit.add_argument("--info")
    edit.add_argument("--replace-token", action="store_true")
    edit.add_argument("--token-file", type=Path)
    edit.add_argument("--api-version")
    status = commands.add_parser("status", help="Verify a stored Page token")
    status.add_argument("account")
    status.add_argument("--api-version")
    post = commands.add_parser("post", help="Publish a text, image, or link post")
    post.add_argument("account")
    content = post.add_mutually_exclusive_group()
    content.add_argument("--text")
    content.add_argument("--text-file", help="UTF-8 file or '-' for stdin")
    post.add_argument("--title")
    post.add_argument("--tag", action="append", default=[])
    post.add_argument("--image", type=Path)
    post.add_argument("--link")
    post.add_argument("--api-version")
    like = commands.add_parser("like", help="Like a post as the selected Page")
    like.add_argument("account")
    like.add_argument("post_id")
    like.add_argument("--api-version")
    comment = commands.add_parser("comment", help="Comment on a post as the selected Page")
    comment.add_argument("account")
    comment.add_argument("post_id")
    content = comment.add_mutually_exclusive_group(required=True)
    content.add_argument("--text")
    content.add_argument("--text-file")
    comment.add_argument("--api-version")
    history = commands.add_parser("history", help="Show action outcomes and saved post IDs")
    history.add_argument("--account")
    history.add_argument("--limit", type=int, default=20)
    commands.add_parser("probe", help="Optional live two-Page integration test")
    return root


def secret(prompt: str) -> str:
    if not sys.stdin.isatty():
        raise ValueError(
            "Use an interactive terminal for secret input. For scripts, set "
            "FBMANAGER_PASSPHRASE and use --token-file when importing a token."
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt)


def unlock(store: Store, *, create: bool = False) -> None:
    if store.cipher is not None:
        return
    passphrase = os.environ.get("FBMANAGER_PASSPHRASE")
    if passphrase is None:
        is_new = store.setting("vault_salt") is None
        passphrase = secret("New vault passphrase: " if is_new else "Vault passphrase: ")
        if is_new and passphrase != secret("Repeat vault passphrase: "):
            raise ValueError("Passphrases do not match.")
    store.unlock(passphrase, create=create)


def read_token(path: Path | None) -> str:
    if path is None:
        return secret("Page access token (hidden): ").strip()
    return (sys.stdin.read() if str(path) == "-" else path.read_text()).strip()


def api_version(store: Store, args) -> str:
    value = (
        getattr(args, "api_version", None)
        or os.environ.get("FBMANAGER_API_VERSION")
        or store.setting("api_version")
    )
    if not value and sys.stdin.isatty():
        value = input("Supported Graph API version from your Meta app (vNN.0): ").strip()
        if valid_version(value):
            store.set_setting("api_version", value)
    if not value or not valid_version(value):
        raise ValueError(
            "Configure a supported version with 'fbmanager configure --api-version vNN.0' "
            "(replace vNN.0 with your app's version)."
        )
    return value


def read_text(args) -> str:
    filename = getattr(args, "text_file", None)
    if filename:
        return sys.stdin.read() if filename == "-" else Path(filename).read_text(encoding="utf-8")
    return getattr(args, "text", None) or ""


def post_content(args) -> tuple[str, Path | None]:
    parts = [part.strip() for part in [args.title or "", read_text(args)] if part.strip()]
    if args.tag:
        if any(not tag.lstrip("#") or any(c.isspace() for c in tag) for tag in args.tag):
            raise ValueError("Each --tag must be one nonempty hashtag without spaces.")
        parts.append(" ".join("#" + tag.lstrip("#") for tag in args.tag))
    if args.link:
        parsed = urlparse(args.link)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("--link must be an HTTP(S) URL.")
    image = args.image.expanduser().resolve() if args.image else None
    if image and args.link:
        raise ValueError("Choose either --image or --link for a post.")
    if image and (
        not image.is_file() or image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
    ):
        raise ValueError("--image must be an existing JPG, PNG, or WebP file.")
    if not parts and not image and not args.link:
        raise ValueError("Provide text, an image, or a link to publish.")
    return "\n\n".join(parts), image


def emit(console: Console, value, *, machine: bool = False):
    if machine:
        print(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, list):
        if not value:
            console.print("No records.")
            return
        columns = (
            ["name", "page_name", "page_id", "info"]
            if "name" in value[0]
            else ["id", "account", "kind", "status", "result", "error"]
        )
        table = Table(*columns, highlight=False)
        for row in value:
            table.add_row(*(Text(str(row.get(column) or "—")) for column in columns))
        console.print(table)
    else:
        for key, item in value.items():
            if item is not None:
                if key == "pages" and isinstance(item, list):
                    table = Table("ID", "Name", highlight=False)
                    for row in item:
                        table.add_row(Text(row["id"]), Text(row["name"]))
                    console.print(table)
                elif isinstance(item, (dict, list)):
                    console.print(f"{key}:\n{json.dumps(item, ensure_ascii=False, indent=2)}")
                else:
                    console.print(f"{key}: {item}")


def perform_action(store: Store, args) -> dict:
    account = store.account(args.account)
    target, image, message = None, None, ""
    if args.command == "post":
        message, image = post_content(args)
    else:
        target = args.post_id
        if not valid_id(target):
            raise ValueError("Use the Graph API post ID, e.g. PAGE_ID_POST_ID, not a URL.")
        if args.command == "comment":
            message = read_text(args).strip()
            if not message:
                raise ValueError("Comment text must not be empty.")
    version = api_version(store, args)
    unlock(store)
    with store.lock(account), GraphClient(version) as client:
        page = Page(account["page_id"], store.token(account))
        action_id = store.begin_action(account, args.command, target, message)
        submitted = False
        try:
            client.verify_identity(page)
            store.update_action(action_id, "unknown")
            submitted = True
            if args.command == "post":
                result = client.publish_post(page, message, image=image, link=args.link)
            elif args.command == "like":
                client.like(page, target)
                result = {"post_id": target, "url": post_url(target)}
            else:
                comment_id = client.comment(page, target, message)
                result = {"post_id": target, "comment_id": comment_id, "url": post_url(target)}
            store.update_action(action_id, "succeeded", result=result)
            return {
                "action_id": action_id,
                "account": account["name"],
                "status": "succeeded",
                **result,
            }
        except GraphError as error:
            store.update_action(
                action_id,
                "unknown" if error.unknown else "failed",
                error=json.dumps(error.details()),
            )
            raise GraphError(
                f"{error} Action ID: {action_id}",
                unknown=error.unknown,
                status=error.status,
                code=error.code,
                subcode=error.subcode,
            ) from None
        except (OSError, sqlite3.Error):
            raise GraphError(
                f"Local storage failed. Check Facebook and history before retrying. "
                f"Action ID: {action_id}",
                unknown=submitted,
            ) from None
        except (KeyboardInterrupt, EOFError):
            if not submitted:
                store.update_action(action_id, "failed", error="Interrupted before submission.")
            raise


def execute(store: Store, args):
    if args.command == "auth":
        from fbmanager.setup_cli import execute_auth

        return execute_auth(store, args)
    if args.command == "accounts" and args.account_command in {
        "import",
        "refresh-token",
    }:
        from fbmanager.setup_cli import execute_accounts

        return execute_accounts(store, args)
    if args.command == "configure":
        if args.api_version:
            if not valid_version(args.api_version):
                raise ValueError("Version must look like vNN.0, using a supported Meta version.")
            store.set_setting("api_version", args.api_version)
        return {"api_version": store.setting("api_version"), "data_directory": str(store.root)}
    if args.command == "accounts":
        if args.account_command == "list":
            return store.list_accounts()
        if args.account_command == "add":
            validate_name(args.name)
            version = api_version(store, args)
            page = Page(args.page_id, read_token(args.token_file))
            with GraphClient(version) as client:
                info = client.page_info(page)
            unlock(store, create=True)
            return store.add(args.name, args.info, page.page_id, info["name"], page.token)
        account = store.account(args.account)
        if args.name is not None:
            validate_name(args.name)
        if args.replace_token or args.token_file:
            version = api_version(store, args)
            page = Page(account["page_id"], read_token(args.token_file))
            with store.lock(account), GraphClient(version) as client:
                info = client.page_info(page)
                unlock(store)
                return store.edit(
                    args.account, args.name, args.info, token=page.token, page_name=info["name"]
                )
        return store.edit(args.account, args.name, args.info)
    if args.command == "status":
        account = store.account(args.account)
        version = api_version(store, args)
        unlock(store)
        with store.lock(account), GraphClient(version) as client:
            info = client.page_info(Page(account["page_id"], store.token(account)))
        return {
            "account": args.account,
            "page_id": info["id"],
            "page_name": info["name"],
            "status": "token_valid",
            "last_token_inspection": store.page_token_metadata(info["id"]),
            "note": "Action permissions are checked by Facebook when used.",
        }
    if args.command == "history":
        if not 1 <= args.limit <= 1000:
            raise ValueError("--limit must be between 1 and 1000.")
        return store.history(args.account, args.limit)
    return perform_action(store, args)


def choose_account(store: Store, console: Console) -> str:
    accounts = store.list_accounts()
    if not accounts:
        raise ValueError("Add a Page first.")
    for number, account in enumerate(accounts, 1):
        console.print(
            f"{number}. {account['name']} — {account['page_name']} ({account['page_id']})"
        )
    value = input("Select Page number: ").strip()
    if not value.isdigit() or not 1 <= int(value) <= len(accounts):
        raise ValueError("Select one of the listed Page numbers.")
    return accounts[int(value) - 1]["name"]


def choose_post(store: Store, console: Console) -> str:
    posts = [
        entry
        for entry in store.history(None, 100)
        if entry["kind"] == "post"
        and entry["status"] == "succeeded"
        and (entry["result"] or {}).get("post_id")
    ]
    for number, entry in enumerate(posts[:10], 1):
        console.print(
            f"{number}. {entry['account']}: {entry['result']['post_id']} "
            f"{(entry['message'] or '')[:60]}"
        )
    value = input("Enter post ID, or #number from the saved posts: ").strip()
    if value.startswith("#"):
        number = value[1:]
        if not number.isdigit() or not 1 <= int(number) <= min(len(posts), 10):
            raise ValueError("Invalid saved-post selection.")
        return posts[int(number) - 1]["result"]["post_id"]
    return value


def menu(store: Store, console: Console) -> int:
    if not sys.stdin.isatty():
        raise ValueError("The menu requires a terminal. Use explicit commands for scripts.")
    while True:
        console.print("\n1. Add Page\n2. List Pages\n3. Edit Page\n4. Perform action\n0. Exit")
        choice = input("> ").strip()
        if choice == "0":
            return 0
        try:
            if choice == "1":
                console.print(
                    "1. Attach with Page token\n2. Import existing Page from authorization\n"
                    "3. Set up authorization\n4. List authorized Pages\n"
                    "5. Renew User authorization\n6. Refresh saved Page token\n0. Back"
                )
                mode = input("Select option: ").strip()
                if mode == "0":
                    continue
                if mode in {"2", "3", "4", "5", "6"}:
                    if mode == "2":
                        argv = [
                            "accounts",
                            "import",
                            input("Local name: ").strip(),
                            "--info",
                            input("Local note (optional): "),
                        ]
                    elif mode == "3":
                        argv = ["auth", "setup"]
                    elif mode == "4":
                        argv = ["auth", "pages"]
                    elif mode == "5":
                        argv = ["auth", "renew"]
                    else:
                        argv = ["accounts", "refresh-token", choose_account(store, console)]
                    emit(console, execute(store, parser().parse_args(argv)))
                    continue
                if mode != "1":
                    raise ValueError("Choose an option from 0 to 6.")
                argv = [
                    "accounts",
                    "add",
                    input("Local name: ").strip(),
                    "--page-id",
                    input("Facebook Page ID: ").strip(),
                    "--info",
                    input("Short description: "),
                ]
            elif choice == "2":
                argv = ["accounts", "list"]
            elif choice == "3":
                name = choose_account(store, console)
                argv = ["accounts", "edit", name]
                new_name = input("New local name (Enter keeps it): ").strip()
                info = input("New description (Enter keeps it): ")
                if new_name:
                    argv += ["--name", new_name]
                if info:
                    argv += ["--info", info]
                if input("Replace token? [y/N]: ").strip().lower() == "y":
                    argv += ["--replace-token"]
            elif choice == "4":
                name = choose_account(store, console)
                action = input("Action: 1=post, 2=like, 3=comment: ").strip()
                if action == "1":
                    argv = ["post", name, "--text", input("Post text: ")]
                    title = input("Title (optional): ")
                    tags = input("Hashtags separated by spaces (optional): ").split()
                    image = input("Image file (optional): ").strip()
                    link = input("Link URL (optional; leave empty for image posts): ").strip()
                    if title:
                        argv += ["--title", title]
                    for tag in tags:
                        argv += ["--tag", tag]
                    if image:
                        argv += ["--image", image]
                    if link:
                        argv += ["--link", link]
                elif action in {"2", "3"}:
                    argv = [
                        "like" if action == "2" else "comment",
                        name,
                        choose_post(store, console),
                    ]
                    if action == "3":
                        argv += ["--text", input("Comment: ")]
                else:
                    raise ValueError("Choose action 1, 2, or 3.")
                preview = parser().parse_args(argv)
                console.print(f"Page: {name}; action: {preview.command}")
                if preview.command == "post":
                    text, image = post_content(preview)
                    console.print(
                        f"Text: {text}\nImage: {image or 'none'}\nLink: {preview.link or 'none'}"
                    )
                else:
                    console.print(f"Post ID: {preview.post_id}")
                    if preview.command == "comment":
                        console.print(f"Comment: {preview.text}")
                if input("Submit? [y/N]: ").strip().lower() != "y":
                    continue
            else:
                raise ValueError("Choose 0, 1, 2, 3, or 4.")
            emit(console, execute(store, parser().parse_args(argv)))
        except (ValueError, GraphError, OSError, sqlite3.Error, getpass.GetPassWarning) as error:
            if isinstance(error, GraphError):
                emit(
                    console, {"status": "unknown" if error.unknown else "failed", **error.details()}
                )
            elif isinstance(error, (OSError, sqlite3.Error)):
                console.print("Local storage error. Check history before retrying an action.")
            else:
                console.print(str(error))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "probe":
        from fbmanager.probe_cli import main as probe_main

        return probe_main(argv)
    command_parser = parser()
    args = command_parser.parse_args(argv)
    console = Console(markup=False, highlight=False)
    if args.command is None and not sys.stdin.isatty():
        command_parser.print_help()
        return 0
    try:
        if args.command == "probe":
            raise ValueError("Use 'fbmanager probe' without --json for the integration test.")
        with Store() as store:
            if args.command in {None, "menu"}:
                return menu(store, console)
            emit(console, execute(store, args), machine=args.json)
        return 0
    except GraphError as error:
        emit(
            console,
            {"status": "unknown" if error.unknown else "failed", **error.details()},
            machine=args.json,
        )
        return 3 if error.unknown else 1
    except (ValueError, getpass.GetPassWarning) as error:
        emit(console, {"status": "failed", "error": str(error)}, machine=args.json)
        return 2
    except (OSError, sqlite3.Error):
        emit(
            console,
            {
                "status": "failed",
                "error": "Local storage error. "
                "Check history and Facebook before retrying an action.",
            },
            machine=args.json,
        )
        return 1
    except (KeyboardInterrupt, EOFError):
        emit(
            console,
            {
                "status": "interrupted",
                "error": "Check history before retrying; a submitted action may have succeeded.",
            },
            machine=args.json,
        )
        return 130
