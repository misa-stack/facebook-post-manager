"""Small Graph API client. Credentials and raw server errors never enter reports."""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class AccessToken:
    """User/app authorization, distinct from a Page identity."""

    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not self.token
            or not self.token.isascii()
            or any(ord(c) < 33 or ord(c) == 127 for c in self.token)
        ):
            raise ValueError("A credential must be nonempty ASCII text without whitespace.")


def valid_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]+(?:_[0-9]+)?", value) is not None


def valid_version(value: str) -> bool:
    return re.fullmatch(r"v[1-9][0-9]*\.[0-9]+", value) is not None


@dataclass(frozen=True)
class Page:
    page_id: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9]+", self.page_id) is None:
            raise ValueError("Page IDs must contain digits only.")
        if not self.token or not self.token.isascii() or any(c.isspace() for c in self.token):
            raise ValueError("A token must be nonempty ASCII text without whitespace.")
        if any(ord(c) < 33 or ord(c) == 127 for c in self.token):
            raise ValueError("A token must not contain control characters.")


class GraphError(Exception):
    def __init__(
        self,
        message: str,
        *,
        unknown: bool = False,
        status: int | None = None,
        code: int | None = None,
        subcode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.unknown = unknown
        self.status = status
        self.code = code
        self.subcode = subcode

    def details(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "http_status": self.status,
            "code": self.code,
            "subcode": self.subcode,
        }


def _integer(value: object) -> int | None:
    return value if type(value) is int else None


def _error_hint(code: int | None, status: int) -> str:
    if code == 190:
        return "Token is expired, invalid, or revoked. Renew authorization or obtain a fresh token."
    if code in {10, 200, 283, 294} or status == 403:
        return "Access denied. Check permissions, Page tasks, app access, and target restrictions."
    if code in {4, 17, 32, 613} or status == 429:
        return "Rate limit reached. Wait before making another request."
    if code == 100:
        return "Object, parameter, or operation unavailable. Check IDs and API access restrictions."
    return (
        "Facebook rejected the request. Check the numeric error codes and Meta app configuration."
    )


class GraphClient:
    def __init__(self, version: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not valid_version(version):
            raise ValueError(
                "API version must look like vNN.0; use your Meta app's supported version."
            )
        self.version = version
        self._client = httpx.Client(
            base_url=f"https://graph.facebook.com/{version}/",
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        page: Page | AccessToken,
        data: dict[str, str] | None = None,
        files: dict | None = None,
    ) -> dict[str, Any]:
        writing = method == "POST"
        try:
            response = self._client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {page.token}"},
                params=data if not writing else None,
                data=data if writing else None,
                files=files,
            )
        except httpx.RequestError:
            raise GraphError(
                "Network request did not complete."
                + (
                    " The action may have succeeded; inspect Facebook before retrying."
                    if writing
                    else " Check connectivity and try again."
                ),
                unknown=writing,
            ) from None
        if response.status_code >= 500:
            raise GraphError(
                "Facebook server error."
                + (
                    " The action may have succeeded; inspect Facebook before retrying."
                    if writing
                    else " Try again later."
                ),
                unknown=writing,
                status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
            code = _integer(error.get("code"))
            raise GraphError(
                _error_hint(code, response.status_code),
                status=response.status_code,
                code=code,
                subcode=_integer(error.get("error_subcode")),
            )
        if not response.is_success:
            raise GraphError(
                _error_hint(None, response.status_code),
                status=response.status_code,
                unknown=writing and response.is_redirect,
            )
        if not isinstance(payload, dict):
            raise GraphError(
                "Unexpected API response; inspect Facebook before retrying.",
                unknown=writing,
                status=response.status_code,
            )
        return payload

    def verify_identity(self, page: Page) -> None:
        payload = self._request("GET", "me", page, {"fields": "id"})
        if payload.get("id") != page.page_id:
            raise GraphError(
                "Token identity does not match the supplied Page ID. Use its Page token."
            )

    def publish(self, page: Page, text: str) -> str:
        payload = self._request("POST", f"{page.page_id}/feed", page, {"message": text})
        return self._created_id(payload)

    def page_info(self, page: Page) -> dict:
        payload = self._request("GET", "me", page, {"fields": "id,name"})
        if payload.get("id") != page.page_id:
            raise GraphError("Token identity does not match this Page ID. Use a Page access token.")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise GraphError("Facebook did not return a Page name. Check the token and access.")
        return {"id": page.page_id, "name": name.replace(page.token, "[redacted]")}

    def publish_post(
        self, page: Page, text: str, *, link: str | None = None, image: Path | None = None
    ) -> dict:
        if image and link:
            raise ValueError("Choose either an image post or a link-preview post.")
        if image:
            mime = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
            with image.open("rb") as stream:
                payload = self._request(
                    "POST",
                    f"{page.page_id}/photos",
                    page,
                    {"message": text, "published": "true"},
                    files={"source": (image.name, stream, mime)},
                )
            photo_id = self._created_id(payload)
            post_id = payload.get("post_id")
            if valid_id(post_id):
                return {"post_id": post_id, "photo_id": photo_id, "url": post_url(post_id)}
            return {
                "photo_id": photo_id,
                "post_id": None,
                "url": f"https://www.facebook.com/photo.php?fbid={photo_id}",
                "warning": "Photo published; Facebook returned no feed post ID. "
                "Do not use the photo ID as a post ID.",
            }
        data = {"message": text}
        if link:
            data["link"] = link
        post_id = self._created_id(self._request("POST", f"{page.page_id}/feed", page, data))
        return {"post_id": post_id, "url": post_url(post_id)}

    def like(self, page: Page, post_id: str) -> None:
        self._check_id(post_id)
        payload = self._request("POST", f"{post_id}/likes", page)
        if payload.get("success") is not True:
            raise GraphError(
                "No explicit like success returned; inspect Facebook before retrying.", unknown=True
            )

    def comment(self, page: Page, post_id: str, text: str) -> str:
        self._check_id(post_id)
        payload = self._request("POST", f"{post_id}/comments", page, {"message": text})
        return self._created_id(payload)

    @staticmethod
    def _check_id(object_id: str) -> None:
        if not valid_id(object_id):
            raise ValueError(
                "Object IDs must be numeric or use the numeric_page_numeric_post form."
            )

    @staticmethod
    def _created_id(payload: dict[str, Any]) -> str:
        object_id = payload.get("id")
        if not valid_id(object_id):
            raise GraphError(
                "Creation response has no valid ID; inspect Facebook before retrying.", unknown=True
            )
        return object_id


def post_url(post_id: str) -> str:
    GraphClient._check_id(post_id)
    return "https://www.facebook.com/" + post_id.replace("_", "/posts/", 1)
