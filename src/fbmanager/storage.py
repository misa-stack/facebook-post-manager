"""Page metadata and history, with passphrase-encrypted access tokens."""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

if os.name == "nt":
    import msvcrt
else:
    import fcntl

IS_WINDOWS = os.name == "nt"


class VaultError(ValueError):
    pass


def data_directory() -> Path:
    explicit = os.environ.get("FBMANAGER_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "FBManager"
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local/share"
    return base / "fbmanager"


def validate_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("Use 1–64 letters, digits, underscores, or hyphens for the local name.")
    return name


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or data_directory()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not IS_WINDOWS:
            self.root.chmod(0o700)
        self.locks = self.root / "locks"
        self.locks.mkdir(mode=0o700, exist_ok=True)
        database = self.root / "pages.sqlite3"
        fd = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if not IS_WINDOWS:
            database.chmod(0o600)
        self.db = sqlite3.connect(database, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pages (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, info TEXT NOT NULL,
                page_id TEXT NOT NULL UNIQUE, page_name TEXT NOT NULL, token BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES pages(id),
                kind TEXT NOT NULL, target TEXT, message TEXT, status TEXT NOT NULL,
                result TEXT, error TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS authorization (
                id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS token_metadata (
                page_id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
        """)
        self.cipher: Fernet | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cipher = None
        self.db.close()

    def setting(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, value))

    def unlock(self, passphrase: str, *, create: bool = False) -> None:
        if not passphrase:
            raise VaultError("Vault passphrase must not be empty.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            salt = self.setting("vault_salt")
            if salt is None and not create:
                raise VaultError("No token vault exists yet. Add a Page or run 'auth setup' first.")
            salt_bytes = base64.urlsafe_b64decode(salt) if salt else os.urandom(16)
            key = Scrypt(salt=salt_bytes, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
            cipher = Fernet(base64.urlsafe_b64encode(key))
            if salt:
                try:
                    check = cipher.decrypt((self.setting("vault_check") or "").encode())
                    if check != b"fbmanager-page-vault-v1":
                        raise InvalidToken
                except InvalidToken:
                    raise VaultError("Wrong vault passphrase or damaged vault.") from None
            else:
                self.db.executemany(
                    "INSERT INTO settings(key,value) VALUES (?,?)",
                    [
                        ("vault_salt", base64.urlsafe_b64encode(salt_bytes).decode()),
                        ("vault_check", cipher.encrypt(b"fbmanager-page-vault-v1").decode()),
                    ],
                )
        self.cipher = cipher

    def _encrypt(self, token: str) -> bytes:
        if self.cipher is None:
            raise VaultError("Unlock the token vault first.")
        return self.cipher.encrypt(token.encode())

    def token(self, account: dict) -> str:
        if self.cipher is None:
            raise VaultError("Unlock the token vault first.")
        row = self.db.execute("SELECT token FROM pages WHERE id=?", (account["id"],)).fetchone()
        if row is None:
            raise ValueError("Page not found.")
        try:
            return self.cipher.decrypt(row[0]).decode()
        except (InvalidToken, UnicodeError):
            raise VaultError(
                "Could not decrypt this Page token. Replace it or restore your backup."
            ) from None

    def add(
        self,
        name: str,
        info: str,
        page_id: str,
        page_name: str,
        token: str,
    ) -> dict:
        validate_name(name)
        encrypted = self._encrypt(token)
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(
                    "INSERT INTO pages VALUES (?,?,?,?,?,?)",
                    (uuid4().hex, name, info, page_id, page_name, encrypted),
                )
        except sqlite3.IntegrityError:
            raise ValueError("This local name or Facebook Page ID is already saved.") from None
        return self.account(name)

    def account(self, name: str) -> dict:
        row = self.db.execute(
            "SELECT id,name,info,page_id,page_name FROM pages WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            raise ValueError("Page not found. Use 'fbmanager accounts list'.")
        return dict(row)

    def list_accounts(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT id,name,info,page_id,page_name FROM pages ORDER BY name"
            )
        ]

    def edit(
        self,
        name: str,
        new_name: str | None,
        info: str | None,
        *,
        token: str | None = None,
        page_name: str | None = None,
    ) -> dict:
        account = self.account(name)
        name = validate_name(new_name) if new_name is not None else name
        encrypted = self._encrypt(token) if token is not None else None
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(
                    "UPDATE pages SET name=?,info=?,page_name=?,token=COALESCE(?,token) WHERE id=?",
                    (
                        name,
                        info if info is not None else account["info"],
                        page_name or account["page_name"],
                        encrypted,
                        account["id"],
                    ),
                )
                if token is not None:
                    self.db.execute(
                        "DELETE FROM token_metadata WHERE page_id=?", (account["page_id"],)
                    )
        except sqlite3.IntegrityError:
            raise ValueError("That local name is already in use.") from None
        return self.account(name)

    @contextmanager
    def lock(self, account: dict):
        fd = os.open(self.locks / f"{account['id']}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                if IS_WINDOWS:
                    # Windows locks a byte range. Seed the file only when empty;
                    # do not write through a lock held by a different process.
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"\0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    except OSError:
                        # CRT implementations may not provide EACCES on contention.
                        # Any failed lock must prevent the protected operation.
                        raise BlockingIOError from None
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, PermissionError):
                raise ValueError(
                    "Another command is using this Page. Wait for it to finish."
                ) from None
            yield
        finally:
            os.close(fd)

    def begin_action(self, account: dict, kind: str, target: str | None, message: str) -> str:
        action_id = uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO actions(id,account_id,kind,target,message,status,created_at) "
                "VALUES (?,?,?,?,?,'prepared',?)",
                (action_id, account["id"], kind, target, message, datetime.now(UTC).isoformat()),
            )
        return action_id

    def update_action(
        self, action_id: str, status: str, *, result: dict | None = None, error: str | None = None
    ) -> None:
        with self.db:
            self.db.execute(
                "UPDATE actions SET status=?,result=?,error=? WHERE id=?",
                (status, json.dumps(result) if result else None, error, action_id),
            )

    def history(self, name: str | None, limit: int = 20) -> list[dict]:
        account_id = self.account(name)["id"] if name else None
        rows = self.db.execute(
            "SELECT actions.*,pages.name AS account FROM actions "
            "JOIN pages ON actions.account_id=pages.id "
            "WHERE (? IS NULL OR account_id=?) ORDER BY created_at DESC LIMIT ?",
            (account_id, account_id, limit),
        )
        result = []
        for row in rows:
            entry = dict(row)
            entry["result"] = json.loads(entry["result"]) if entry["result"] else None
            result.append(entry)
        return result

    def save_authorization(self, payload: dict) -> None:
        encrypted = self._encrypt(json.dumps(payload))
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO authorization VALUES (1,?)", (encrypted,))

    def authorization(self) -> dict:
        if self.cipher is None:
            raise VaultError("Unlock the token vault first.")
        row = self.db.execute("SELECT payload FROM authorization WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Run 'fbmanager auth setup' first.")
        try:
            return json.loads(self.cipher.decrypt(row[0]).decode())
        except (InvalidToken, ValueError, UnicodeError):
            raise VaultError(
                "Could not decrypt authorization. Restore your vault backup."
            ) from None

    def save_token_metadata(self, page_id: str, metadata: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO token_metadata VALUES (?,?)",
                (page_id, json.dumps(metadata)),
            )

    def page_token_metadata(self, page_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT payload FROM token_metadata WHERE page_id=?", (page_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None
