# FBManager

Manage Facebook Pages from the terminal on Windows and Linux. FBManager uses
the Graph API to publish posts, like and comment as a saved Page, and keep a
local history of actions.

Use individual commands or an interactive menu to add Pages, list Pages, edit
their local details, and perform actions. The application works with existing
Pages you are authorized to manage. It does not create Facebook accounts or
Pages, log in through a browser, or publish as a personal profile.

## Features

- Save multiple Pages under local nicknames.
- Import Pages through User authorization or add a Page access token directly.
- Publish text, a single image, or a link preview, with optional titles and hashtags.
- Like and comment as a saved Page using a Graph API post ID.
- Check authorization and refresh saved Page tokens.
- Store encrypted credentials and SQLite action history locally.
- Return JSON for use in scripts.

## Run from source

You need **Python 3.11 or newer**, `pip`, and virtual environment support. Internet
access is required to install dependencies and call Facebook.

Clone this repository, or select **Code → Download ZIP** on GitHub and extract it.
Open a terminal in the repository folder containing `README.md` and `src/`.

This checkout contains the application source without packaging metadata or
bundled executables. Install its dependencies and run the module directly:

### Linux (Bash or Zsh)

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "httpx>=0.28,<1" "rich>=14,<15" "cryptography>=44,<49"
export PYTHONPATH="$PWD/src"
python -m fbmanager --help
```

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "httpx>=0.28,<1" "rich>=14,<15" "cryptography>=44,<49"
$env:PYTHONPATH = (Join-Path $PWD "src")
.\.venv\Scripts\python.exe -m fbmanager --help
```

The examples below use `python -m fbmanager`. On Windows, replace `python` with
`.\.venv\Scripts\python.exe`. Run them from the repository folder. In a new
terminal, set `PYTHONPATH` again and, on Linux, activate `.venv` again; dependency
installation is only needed once.

## Connect a Page

Have an existing Facebook Page you can manage and a Meta app configured for the
Page actions you want to use. Prepare either:

- The app's **App ID**, **App Secret**, and a **User access token** for that app,
  to import your authorized Pages; or
- A **Page access token** and the matching numeric **Page ID**, to add a Page directly.

Use a Graph API version supported by your Meta app. FBManager asks for it on
the first API operation and saves it. Enter the actual version in `vNN.0`
format; `vNN.0` is a placeholder, not a version to copy.

### Import through User authorization

```sh
python -m fbmanager auth setup
python -m fbmanager auth pages
python -m fbmanager accounts import mypage
```

`auth setup` prompts for the App ID, App Secret, User token, and a new vault
passphrase. Secret inputs are hidden. It exchanges the User token and stores
the authorization encrypted. `accounts import` lets you choose an existing
Page and saves its verified Page token under the nickname `mypage`.
User authorization must include `pages_show_list` for listing and importing Pages.

### Add a Page token directly

If you already have a Page token, use this instead of the import steps:

```sh
python -m fbmanager accounts add mypage --page-id YOUR_NUMERIC_PAGE_ID --info "My Page"
```

Replace `YOUR_NUMERIC_PAGE_ID` with the matching Page ID. The command asks for
the token in a hidden prompt, verifies it, and encrypts it using your vault
passphrase. The nickname is local to FBManager.

Check your saved Page:

```sh
python -m fbmanager accounts list
python -m fbmanager status mypage
```

## Usage

Open the interactive menu:

```sh
python -m fbmanager
```

The menu previews actions and asks for confirmation. **Direct `post`, `like`,
and `comment` commands submit immediately.**

### Publish a post

Each command below publishes a separate post:

```sh
python -m fbmanager post mypage --text "Hello from our Page"
python -m fbmanager post mypage --text "Our latest photo" --image photo.jpg
python -m fbmanager post mypage --text "Read more" --link https://example.com
python -m fbmanager post mypage --text-file post.txt --title "News" --tag updates
```

Text files use UTF-8. Repeat `--tag` for multiple hashtags. Choose either
`--image` or `--link` for a post; they cannot be combined.

### Comments, likes, and history

Replace `POST_ID` with a Graph API post ID, such as an ID returned by a publish
command. A Facebook web URL is not a post ID.

```sh
python -m fbmanager comment mypage POST_ID --text "Thanks for reading!"
python -m fbmanager like mypage POST_ID
python -m fbmanager history --account mypage --limit 10
python -m fbmanager --json accounts list
```

Meta determines which targets and actions your authorization permits. A saved
token or a post ID does not guarantee access to another Page's content.

### Renew authorization

For Pages imported through User authorization, obtain a fresh User token for
the same app and User, then run:

```sh
python -m fbmanager auth renew
python -m fbmanager accounts refresh-token mypage
```

Repeat `accounts refresh-token` for each saved Page you want to refresh.
Renewing User authorization does not automatically replace saved Page tokens.
For a manually added Page, replace its token with:

```sh
python -m fbmanager accounts edit mypage --replace-token
```

Use `python -m fbmanager --help` or append `--help` to a subcommand for its options.

## Local data and scripting

FBManager creates `pages.sqlite3` in the following directory:

| Platform | Default data directory |
| --- | --- |
| Linux | `$XDG_DATA_HOME/fbmanager`, or `~/.local/share/fbmanager` |
| Windows | `%LOCALAPPDATA%\FBManager` |

Credentials are encrypted using a key derived from your vault passphrase.
Page metadata and action history are not encrypted. Keep the passphrase safe;
back up the database while the application is closed, and keep it out of Git.

| Environment variable | Purpose |
| --- | --- |
| `FBMANAGER_HOME` | Override the local data directory. |
| `FBMANAGER_API_VERSION` | Set the API version; a command's `--api-version` takes precedence. |
| `FBMANAGER_PASSPHRASE` | Unlock the vault without an interactive prompt. |

For scripts, supply secrets through a secure environment or the supported
credential-file options shown by `--help`. Use
`accounts import mypage --page-id YOUR_NUMERIC_PAGE_ID` for non-interactive
imports. Put `--json` before the subcommand for machine-readable output.

Do not commit tokens, App Secrets, passphrases, or databases. Avoid HTTP debug
logging: token exchange and inspection requests carry secrets in HTTPS query
parameters.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `No module named fbmanager` | Run from the repository folder and set `PYTHONPATH` to its `src` directory. |
| A dependency cannot be imported | Use the same virtual environment Python for dependency installation and execution. |
| Nothing appears while entering a secret | Hidden input is intentional. Paste once and press Enter. |
| Wrong vault passphrase | Enter the passphrase you chose for FBManager, not your Facebook password. |
| No authorized Pages | Check Page access and the app's authorization, then renew the User token. |
| Expired token or access denied | Check the token, app permissions, Page access, and target restrictions; renew or replace the token as needed. |
| Action status is `unknown` | Check Facebook and local history before retrying; the action may already have succeeded. |

FBManager does not automatically retry ambiguous writes. Live behavior depends
on your credentials and Meta's access checks; publishing has not been verified
with your account.

## Source layout

| File | Responsibility |
| --- | --- |
| [cli.py](src/fbmanager/cli.py) | Command parsing, interactive menu, and action execution. |
| [setup_cli.py](src/fbmanager/setup_cli.py) | Authorization, Page import, and token renewal commands. |
| [graph.py](src/fbmanager/graph.py) | Graph API requests and error handling for Page actions. |
| [management.py](src/fbmanager/management.py) | User authorization and Page-token retrieval. |
| [storage.py](src/fbmanager/storage.py) | SQLite storage and credential encryption. |
| [probe_cli.py](src/fbmanager/probe_cli.py) / [probe.py](src/fbmanager/probe.py) | Optional live two-Page integration probe. |

The `probe` command makes live API calls and is not required for normal use.
