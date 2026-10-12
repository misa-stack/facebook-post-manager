from __future__ import annotations

import argparse
import getpass
import os
import sys
import warnings
from pathlib import Path

from rich.console import Console

from fbmanager.graph import GraphClient, Page, valid_version
from fbmanager.probe import Report, run_probe


def default_report_directory() -> Path:
    configured = os.environ.get("XDG_STATE_HOME", "")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".local/state"
    )
    return base / "fbmanager/reports"


def read_page(label: str) -> Page:
    page_id = input(f"Page {label} ID: ").strip()
    # Refuse getpass's echoing fallback on terminals without secure input.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        token = getpass.getpass(f"Page {label} access token (hidden): ").strip()
    return Page(page_id, token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Facebook Page Manager: optional cross-Page integration test.",
        epilog="Run 'fbmanager probe' after following docs/meta-setup.md. No tokens are saved.",
    )
    commands = parser.add_subparsers(dest="command")
    probe = commands.add_parser("probe", help="Test publishing and cross-Page likes/comments live")
    probe.add_argument("--api-version", help="Supported version shown in your Meta app, e.g. vNN.0")
    probe.add_argument("--report-dir", type=Path, default=default_report_directory())
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    console = Console(markup=False, highlight=False)
    if not sys.stdin.isatty():
        console.print(
            "Run the probe in an interactive terminal; tokens are entered in hidden prompts."
        )
        return 2
    report: Report | None = None
    try:
        version = (
            args.api_version or input("Graph API version shown in your Meta app (vNN.0): ").strip()
        )
        if not valid_version(version):
            raise ValueError("Enter an explicit supported API version such as v25.0, not 'latest'.")
        console.print("Enter two distinct managed Pages. Tokens stay in process memory.")
        pages = (read_page("A"), read_page("B"))
        if pages[0].page_id == pages[1].page_id:
            raise ValueError("Page A and Page B must have different IDs.")
        console.print(
            "This will publish one test post on each Page "
            f"({pages[0].page_id}, {pages[1].page_id}) "
            "and attempt one like and one comment from the other Page on each post.\n"
            "Posts remain on Facebook for inspection. No requests are automatically retried."
        )
        if input("Type RUN to perform this live test: ").strip() != "RUN":
            console.print("Cancelled. No API requests sent.")
            return 0
        report = Report(args.report_dir, version, pages)
        console.print(f"Report: {report.path}")
        with GraphClient(version) as client:
            run_probe(client, pages, report, notify=console.print)
        if report.data["status"] == "awaiting_visual_confirmation":
            console.print("API calls succeeded. Check both posts on Facebook:")
            for entry in report.data["actions"]:
                if entry["action"] == "publish":
                    post_id = entry["result_id"]
                    url = (
                        f"https://www.facebook.com/{post_id.replace('_', '/posts/', 1)}"
                        if "_" in post_id
                        else f"https://www.facebook.com/{post_id}"
                    )
                    console.print(f"  {url}")
            console.print(
                "Verify each post's author is its publishing Page, and the other Page appears "
                "as both the liker and commenter. A personal-profile reaction does not count."
            )
            if (
                input(
                    "After checking all six actions, type VERIFIED (Enter leaves pending): "
                ).strip()
                == "VERIFIED"
            ):
                report.data["visual_confirmation"] = True
                report.finish("passed")
        status = report.data["status"]
        console.print(f"Probe result: {status}. Report: {report.path}")
        if status == "unknown":
            console.print(
                "Inspect Facebook and the report before retrying. Further writes were stopped."
            )
        if status == "passed":
            console.print("The live test passed for these Pages and this app configuration.")
            return 0
        return 1 if status == "failed" else 3
    except (KeyboardInterrupt, EOFError):
        console.print("\nInterrupted. No further requests will be sent.")
        if report:
            console.print(f"Inspect the last checkpoint before retrying: {report.path}")
        return 130
    except (ValueError, getpass.GetPassWarning) as error:
        console.print(str(error))
        return 2
    except OSError:
        console.print(
            "Could not write the report. Stopped; inspect Facebook before retrying any action."
        )
        if report:
            console.print(f"Last checkpoint: {report.path}")
        return 1
