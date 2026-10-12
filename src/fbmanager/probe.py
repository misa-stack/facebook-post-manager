"""Two-way live test with a durable, credential-free action journal."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from fbmanager.graph import GraphClient, GraphError, Page


class Report:
    def __init__(self, directory: Path, version: str, pages: tuple[Page, Page]) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / f"probe-{uuid4().hex}.json"
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "run_id": self.path.stem.removeprefix("probe-"),
            "started_at": datetime.now(UTC).isoformat(),
            "api_version": version,
            "page_ids": [p.page_id for p in pages],
            "status": "not_started",
            "identity_checks": [],
            "actions": [],
            "visual_confirmation": False,
        }
        self.save()

    def save(self) -> None:
        """Replace atomically, preserving the previous checkpoint on write failure."""
        fd, temporary = tempfile.mkstemp(prefix=".probe-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def finish(self, status: str) -> None:
        self.data["status"] = status
        self.save()


def run_probe(
    client: GraphClient,
    pages: tuple[Page, Page],
    report: Report,
    notify: Callable[[str], None] = print,
) -> None:
    if pages[0].page_id == pages[1].page_id:
        raise ValueError("Use two distinct Pages for a cross-Page test.")
    report.finish("checking_identities")
    for page in pages:
        try:
            client.verify_identity(page)
        except GraphError as error:
            report.data["identity_checks"].append(
                {
                    "page_id": page.page_id,
                    "status": "failed",
                    "error": error.details(),
                }
            )
            report.finish("failed")
            notify(f"Page {page.page_id}: {error}")
            return
        report.data["identity_checks"].append({"page_id": page.page_id, "status": "succeeded"})
        report.save()
        notify(f"Verified token identity for Page {page.page_id}.")

    def action(
        name: str,
        actor: Page,
        target: str,
        operation: Callable[[], str | None],
    ) -> tuple[str, str | None]:
        entry: dict[str, Any] = {
            "action": name,
            "actor_page_id": actor.page_id,
            "target_id": target,
            "status": "unknown",
            "started_at": datetime.now(UTC).isoformat(),
        }
        report.data["actions"].append(entry)
        # Checkpoint BEFORE writing so an interruption cannot look like no action happened.
        report.finish("unknown")
        notify(f"{name.capitalize()}: actor {actor.page_id}, target {target} ...")
        try:
            result = operation()
        except GraphError as error:
            entry["status"] = "unknown" if error.unknown else "failed"
            entry["error"] = error.details()
            notify(f"{entry['status']}: {error} (code {error.code}, subcode {error.subcode})")
            report.save()
            return entry["status"], None
        entry["status"] = "succeeded"
        if result is not None:
            entry["result_id"] = result
        report.save()
        notify(f"Succeeded{': ' + result if result else '.'}")
        return "succeeded", result

    run_id = report.data["run_id"]
    for publisher, actor in (pages, tuple(reversed(pages))):
        status, post_id = action(
            "publish",
            publisher,
            publisher.page_id,
            partial(
                client.publish,
                publisher,
                f"FBManager feasibility test {run_id}. Published by Page {publisher.page_id}.",
            ),
        )
        if status == "unknown":
            return
        if post_id is None:
            continue
        status, _ = action("like", actor, post_id, partial(client.like, actor, post_id))
        if status == "unknown":
            return
        status, _ = action(
            "comment",
            actor,
            post_id,
            partial(
                client.comment,
                actor,
                post_id,
                f"FBManager cross-Page test {run_id}. Comment by Page {actor.page_id}.",
            ),
        )
        if status == "unknown":
            return
    actions = report.data["actions"]
    passed = len(actions) == 6 and all(entry["status"] == "succeeded" for entry in actions)
    report.finish("awaiting_visual_confirmation" if passed else "failed")
