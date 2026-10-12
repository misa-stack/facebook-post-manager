"""User authorization and token retrieval for existing Facebook Pages."""

from __future__ import annotations

import re
import time

from fbmanager.graph import AccessToken, GraphClient, GraphError, Page


def numeric(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]+", value) is not None


def expiry(value: object) -> str:
    if value == 0:
        return "no_scheduled_expiry"
    if type(value) is int and value > 0:
        return "expired" if value <= time.time() else "scheduled"
    return "not_reported"


class ManagementClient(GraphClient):
    def inspect_token(
        self,
        app_id: str,
        app_secret: str,
        token: str,
        kind: str,
        *,
        page_id: str | None = None,
    ) -> dict:
        if not numeric(app_id):
            raise ValueError("App ID must contain digits only.")
        AccessToken(app_secret)
        AccessToken(token)
        payload = self._request(
            "GET",
            "debug_token",
            AccessToken(f"{app_id}|{app_secret}"),
            {"input_token": token},
        ).get("data")
        if not isinstance(payload, dict):
            raise GraphError("Token debugger returned no metadata.")
        if payload.get("is_valid") is not True:
            raise GraphError("Authorization is invalid or revoked. Run 'fbmanager auth renew'.")
        if str(payload.get("app_id")) != app_id or payload.get("type") != kind:
            raise GraphError("Token belongs to a different app or has the wrong token type.")
        if kind == "USER" and not numeric(payload.get("user_id")):
            raise GraphError("Token debugger returned no valid User ID.")
        if page_id and payload.get("profile_id") is not None:
            if str(payload["profile_id"]) != page_id:
                raise GraphError("Token debugger identity does not match the expected Page.")
        result = {"app_id": app_id, "type": kind, "is_valid": True}
        if kind == "USER":
            result["user_id"] = payload["user_id"]
        for key in ("expires_at", "data_access_expires_at"):
            value = payload.get(key)
            value = value if type(value) is int and value >= 0 else None
            result[key] = value
            result[f"{key}_status"] = expiry(value)
            if expiry(value) == "expired":
                raise GraphError("Authorization has expired. Run 'fbmanager auth renew'.")
        scopes = payload.get("scopes", [])
        result["scopes"] = (
            [
                scope
                for scope in scopes
                if isinstance(scope, str) and re.fullmatch(r"[a-z_]{1,80}", scope)
            ]
            if isinstance(scopes, list)
            else []
        )
        result["checked_at"] = int(time.time())
        return result

    def exchange(self, app_id: str, app_secret: str, user_token: str) -> dict:
        initial = self.inspect_token(app_id, app_secret, user_token, "USER")
        payload = self._request(
            "GET",
            "oauth/access_token",
            AccessToken(f"{app_id}|{app_secret}"),
            {
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": user_token,
            },
        )
        token = payload.get("access_token")
        if not isinstance(token, str):
            raise GraphError("Token exchange did not return an access token.")
        metadata = self.inspect_token(app_id, app_secret, token, "USER")
        if metadata["user_id"] != initial["user_id"]:
            raise GraphError("Token exchange returned a different User identity.")
        if "pages_show_list" not in metadata["scopes"]:
            raise GraphError("User authorization needs pages_show_list. Grant it and renew.")
        return {
            "app_id": app_id,
            "app_secret": app_secret,
            "user_token": token,
            "user_id": metadata["user_id"],
            "metadata": metadata,
        }

    def check_authorization(self, auth: dict) -> dict:
        result = self.inspect_token(auth["app_id"], auth["app_secret"], auth["user_token"], "USER")
        if result["user_id"] != auth["user_id"]:
            raise GraphError("Stored authorization belongs to a different User.")
        if "pages_show_list" not in result["scopes"]:
            raise GraphError("User authorization needs pages_show_list. Grant it and renew.")
        return result

    def managed_pages(self, auth: dict) -> list[dict]:
        """Follow cursors on a fixed origin, never a server-provided next URL."""
        result, seen = [], set()
        params = {"fields": "id,name", "limit": "100"}
        while True:
            payload = self._request("GET", "me/accounts", AccessToken(auth["user_token"]), params)
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise GraphError("Facebook did not return the managed Pages list.")
            for row in rows:
                if isinstance(row, dict) and numeric(row.get("id")):
                    name = row.get("name")
                    name = name if isinstance(name, str) else ""
                    for key in ("user_token", "app_secret"):
                        name = name.replace(auth[key], "[redacted]")
                    result.append({"id": row["id"], "name": name})
            paging = payload.get("paging") or {}
            if not isinstance(paging, dict) or not paging.get("next"):
                return result
            cursors = paging.get("cursors") or {}
            after = cursors.get("after") if isinstance(cursors, dict) else None
            if not isinstance(after, str) or not after or after in seen or len(seen) >= 1000:
                raise GraphError("Cannot safely continue managed Pages pagination.")
            seen.add(after)
            params["after"] = after

    def page_token(self, auth: dict, page_id: str) -> tuple[Page, dict, dict]:
        if not numeric(page_id):
            raise ValueError("Page ID must contain digits only.")
        payload = self._request(
            "GET", page_id, AccessToken(auth["user_token"]), {"fields": "id,access_token"}
        )
        if payload.get("id") != page_id or not isinstance(payload.get("access_token"), str):
            raise GraphError(
                "Page token unavailable. Authorize this Page, run 'auth renew', "
                "then retry the import or token refresh."
            )
        page = Page(page_id, payload["access_token"])
        metadata = self.inspect_token(
            auth["app_id"],
            auth["app_secret"],
            page.token,
            "PAGE",
            page_id=page_id,
        )
        info = self.page_info(page)
        for key in ("user_token", "app_secret"):
            info["name"] = info["name"].replace(auth[key], "[redacted]")
        return page, info, metadata
