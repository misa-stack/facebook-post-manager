# FBManager — Facebook Pages on Windows and Linux

**Windows:** extract `FBManager-0.5.0-Windows-x64.zip` and double-click `SPUSTIT.bat`.
No Python installation is needed. Read the [Czech Windows quick start](README.windows.cs.md).

**Česky: [jednoduchý návod k použití](README.cs.md) ·
[podrobný návod na získání tokenu](docs/ACCESS_TOKEN.cs.md)**

A CLI with a four-option interactive menu: **add a Page, list Pages, edit
Page details, and perform an action**. It uses the official Graph API and a Page
access token for each saved Page. No browser automation or personal-profile login
is used.

**[Read the usage guide](docs/USAGE.md)** for token setup, all commands, menu use,
examples, and troubleshooting.

## Set up authorization and import an existing Page

For first-time setup, follow the [complete Czech beginner tutorial](docs/START_HERE.cs.md):
a real Facebook profile, manual Page creation on Facebook, one developer registration,
one Meta app, User consent, and importing the existing Page. The app does not create
Facebook accounts or Pages. One developer/app can manage multiple authorized Pages.

```sh
.venv/bin/fbmanager auth setup
.venv/bin/fbmanager auth pages
.venv/bin/fbmanager accounts import shop
.venv/bin/fbmanager post shop --text "Hello from our Page"
```

`auth setup` asks locally for the App ID, hidden App Secret and hidden User token,
then exchanges and encrypts long-lived User authorization. `accounts import` offers
an interactive selection of existing Pages; scripts must supply `--page-id NUMERIC_ID`.
Each imported Page token is checked and encrypted. Tokens can still be revoked.
Renew with `auth renew`, then `accounts refresh-token shop` for each saved Page.

Removed in 0.5.0: `accounts create`, `accounts resume`, `accounts jobs`, and
`auth categories`. Existing credentials and history remain usable. Old creation
records are left in existing databases but are no longer executed or reserved.

## Quick start

In this workspace, the application is installed in `.venv`:

```sh
.venv/bin/fbmanager --help
.venv/bin/fbmanager configure --api-version v26.0
.venv/bin/fbmanager accounts add mypage --page-id YOUR_NUMERIC_PAGE_ID --info "My Page"
.venv/bin/fbmanager post mypage --text "Hello from the terminal"
```

Use the supported API version from your Meta app; `v26.0` above is an example.
Replace `YOUR_NUMERIC_PAGE_ID` with the Page's ID. Adding a Page asks for the Page
access token in a hidden prompt, verifies it, and asks you to choose a vault
passphrase. Later actions unlock that vault; tokens are encrypted with a key
derived from your passphrase and are never printed.

To use the menu:

```sh
.venv/bin/fbmanager
```

For scripts, set `FBMANAGER_PASSPHRASE` securely to avoid the passphrase prompt.
Explicit `post`, `like`, and `comment` commands submit immediately; the menu shows
a preview and asks before submitting.

## Features

- Named Pages with local descriptions; token validation and replacement.
- Existing-Page import, encrypted User authorization, and Page-token renewal.
- Text posts, one-image posts, and link-preview posts; optional title and hashtags.
- Likes and comments as any saved Page, using a Graph API post ID.
- SQLite action history, returned post/comment/photo IDs, and JSON output.
- Encrypted credential storage; no secrets in CLI output or stored request logs.
  Meta's token exchange/debug requests carry secrets in HTTPS query parameters;
  ordinary Page operations use bearer headers. Do not enable HTTP debug logging.
- No automatic retries after ambiguous writes; outcomes are recorded as unknown.

Facebook controls which targets and actions your tokens may access. Supplying a
post ID does not grant access. Cross-Page engagement is attempted through the API
and may be rejected. **Live Facebook actions have not been verified with your
credentials.** Automated tests use mocked HTTP.

## Install elsewhere

With Python 3.11+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/fbmanager --help
```

Alternatively, with `uv`: `uv sync --locked`, then `uv run fbmanager`.
For a user-level command outside this directory: `pipx install .`.

## Development

Windows builds: [build and verification instructions](docs/WINDOWS_BUILD.md).

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

The optional `fbmanager probe` command tests two Pages live; it is not required to
use the application. See [Meta setup and the optional probe](docs/meta-setup.md).

The earlier browser prototype was removed. Existing browser files are not loaded
or deleted. Page metadata and encrypted tokens use a separate `pages.sqlite3`
database under `${XDG_DATA_HOME:-~/.local/share}/fbmanager/`.
