# Tales of Avalar

Campaign information for *Tales of Avalar*, managed with **Chronicler**: a small engine that keeps the world in Markdown files, checks them for problems, and shows a GM or Player version in a local web app where the GM can also edit everything.

## Features

- Entry pages for every type, with computed links (members, cast, who's here, mentions)
- Search with snippets and "did you mean"
- Home page: last session, spotlight, upcoming events, threads, prep notes, drafts
- GM and Player views of the same world (secrets, hidden pages, party beliefs, reveals)
- `validate` and `review` checks
- Browser editing: quick edit, edit mode, toolbar for secrets/prep/reveals, autosaved drafts, live previews
- Create, rename, retire, and delete entries
- Conflict-safe saves (outside edits are never overwritten)
- GM and Player accounts with login
- Dark/light themes and dyslexia-friendly fonts

## Planned

1. Prepared reveals, a Reveal window, and live updates for players
2. Session pipeline: recorded recap, local transcription, approve-to-apply
3. Roll tables and generators (encounters, wild magic, NPCs, shops, rumors)
4. 5e character sheet / stat block generator
5. Calendar, holidays, and map
6. Strategist (optional AI suggestions)
7. Later: combat tracker, remote player access, voice clips, multi-page drafts, multi-GM support

## Running it

### 1. One-time setup
You need **Python 3.13+** and **git**.

```bash
python -m pip install --user uv   # install the uv package manager
python -m uv sync                 # create .venv and install Chronicler + dependencies
```

> `uv` is run as `python -m uv` because the pip-installed `uv` command may not be on your PATH. If `uv` works on its own in your terminal, you can drop the `python -m` prefix everywhere.

### 2. Start the app
From the project folder:

```bash
python -m uv run chronicler serve
```

Then open **http://127.0.0.1:8000** in your browser. Stop the server with **Ctrl+C**.

- Use a different port: `python -m uv run chronicler serve --port 8080`
- Serve a different campaign folder: `python -m uv run chronicler serve path/to/campaign`
- Try the small test world: `python -m uv run chronicler serve tests/fixtures/campaign`

The app only listens on your own computer. It picks up files you change in VS Code: just refresh the page.

### 3. First run: accounts
The first time, the browser asks you to create the **GM account**. After that, everyone logs in.

| | GM | Player |
|---|---|---|
| Sees | GM view (can switch to the Player view) | Player view only |
| Edits | Everything | Nothing |
| Users page, tools | Yes | No |

- **Add a player:** your name (top right) → **Users** → *Add an account*.
- **Reset a password or remove an account:** on the Users page.
- **Locked out or forgot your password?** From the project folder:

  ```bash
  python -m uv run chronicler user reset-password "Your Name"
  python -m uv run chronicler user add "Jo" --role player   # or --role gm
  python -m uv run chronicler user list
  ```

Accounts live in `campaign/_data/chronicler.db` (git-ignored, never committed).

## How to

### Look around
- **GM / Player** (top right, GM accounts only): switch between your full view and what players are allowed to see.
- **☾ / ☀**: dark or light theme. **Aa**: cycle fonts. Both are remembered per browser.
- **Search** (top bar): every reference to a name or keyword, with snippets.
- **Sidebar tools (GM only):** *Party beliefs vs truth*, *Issues* (same checks as `validate`), *Users*.

### Change one field quickly
On an entry page in the GM view, hover a field and click **✎**. Pick or type the new value and **Save**; the page reloads with it. **Cancel** puts the page back. Lists and grouped fields (factions, goals, relationships…) are changed in edit mode instead.

### Edit a page
1. Click **Edit** at the top of the entry page.
2. Change fields in **Fields** and the text in **Text**. Rows such as goals or relationships have **+ Add**, **↑ ↓** to reorder, and **✕** to remove.
3. Your work **autosaves as a draft** 2 seconds after you stop typing (and at least every 10 seconds while you type), only when something changed. The status line says when.
4. **Save** (or **Ctrl+S**) writes it to the page. If something blocks saving (a broken link, a value out of range, an unclosed tag), it is listed with a button to fix it or jump to it; your work stays safely in the draft.
5. **Done** goes back to the page. Leaving with unsaved edits asks: **Save now**, **Keep in draft**, or **Discard**.

Unsaved drafts are listed on the home page, and an entry with a draft shows a **Draft** badge. **Discard draft** throws the draft away and reloads the page as it is on disk.

### Hide or reveal things
- **Hide some text:** select it and click **Secret**. Selecting across an existing secret merges them into one; selecting part of a prep note pulls the whole note inside.
- **Make text public again:** select it (or put the cursor in it) and click **Unmark**. Only the selection becomes public; the rest stays secret.
- **Hide a whole section:** put the cursor under its heading and click **Section secret** (click again to make it public).
- **Hide a whole page:** in edit mode, set **Secret** (Hidden page) to *Yes*. Setting **Status** to anything but *canon* also hides it from players.
- **Reveal a secret:** put the cursor in the secret, click **Reveal**, and pick the session (or a section of its notes) where it was revealed. Players can then read it.
- **Prep notes:** select text and click **Prep**. Prep notes are always GM-only and appear on the home page; click one there to jump to it.
- **A character the party knows by another name:** when creating the entry, tick *The party knows them by another name*. The page id is then made from that name, so the true name never shows in page addresses.

### Create, rename, retire, delete
- **+ New entry** (top bar): pick a type and a name; the id is suggested for you. The new page starts from that type's template and opens in edit mode.
- **Rename…** (edit mode): either keep the old name as an alias (old links keep working) or do a hard rename that rewrites every link; you see the list of files that will change first.
- **Retire…**: the page stays and links keep working, but it is marked retired and players no longer see it.
- **Delete…**: removes the file and its drafts after showing what links to it, and warns if git has changes it never saved.
- Asset entries need their image or audio file in `campaign/assets/files/` before you create the entry.

### If a page was also edited in VS Code
Saving never overwrites changes made outside the app. If the file changed since you started editing, your edits go to `campaign/_drafts/conflicts/<id>-<date>.md`, the editor tells you that path, and it reloads the current file. Open the conflict copy in VS Code to bring your edits back. Drafts use the same format as entry files.

### Check the world for problems
```bash
python -m uv run chronicler validate                 # all errors and warnings
python -m uv run chronicler validate --errors-only   # errors only
```
Run it after editing files by hand; it exits non-zero if there are errors. Edits in the browser are checked automatically before every save.

### After changing an entry's status
```bash
python -m uv run chronicler review               # every status change since your last commit
python -m uv run chronicler review --entry Cas   # one entry, by name, alias, or id
```
Lists everything that links to or mentions the changed entry, its alternate versions, and canon entries still pointing at something retired.

### Edit files by hand
1. Copy the matching template from `campaign/_templates/` into the right folder (e.g. `npc.md` → `campaign/npcs/`).
2. Set a unique `id` (lowercase-with-dashes; it appears in page URLs, so don't put a hidden true name in it) and fill in what you know. Empty fields are fine. Put text containing commas in "quotes" inside `{ ... }` items.
3. Run `chronicler validate`, then check the page in the app.

## Markup

| Thing | Syntax |
|---|---|
| Link | `[[Name]]`, `[[Alias]]`, `[[Name#Section]]`, `[[Name\|shown text]]` |
| Secret | `{secret}…{/secret}` (one continuous tag; may cross lines, not headings) |
| Secret section | `## Heading {secret}` |
| Revealed secret | `{revealed [[Session 7#Notes]]}…{/revealed}` |
| Reveal group / topic (for prepared reveals) | `{secret reveal=vex-letter about=[[Velkyn Plot]]}…{/secret}` |
| Prep note | `{prep}…{/prep}` (listed on the home page; always GM-only) |
| Image / audio | `![[img:asset-id]]`, `[[audio:asset-id]]` |
| Sublocation | `### Name` under a `## Places` heading |

Per field, the Player view shows: the `party_view` value if set, otherwise nothing if the field is secret or GM-only, otherwise the real value. Only `canon` entries are shown to players.

Tags may sit fully inside each other (a prep note inside a secret, a secret inside a reveal); players see text only if every tag around it is visible. Partly overlapping tags are an error. A tag that is never closed hides everything to the end of its section until you fix it.

## Layout

| Path | What it holds |
|---|---|
| `campaign/` | The world: one Markdown file per entry, in a folder per type (`npcs/`, `locations/`, …) |
| `campaign/_templates/` | A blank, commented template for every entry type (new entries start from these) |
| `campaign/assets/` | Image/audio entries; the files themselves go in `campaign/assets/files/` |
| `campaign/_drafts/` | Editor drafts, and `conflicts/` copies (git-ignored) |
| `campaign/_data/` | The accounts database (git-ignored) |
| `campaign/calendar.yaml` | In-world calendar (months, weekdays, seasons, eras) |
| `lib/source-files/` | The original Word-exported docs the world was migrated from |
| `src/chronicler/` | The engine (campaign-agnostic) |
| `tests/` | Tests, including a fixture campaign full of planted secrets |

## Development

```bash
python -m uv run pytest --cov            # tests + coverage
python -m uv run ruff check src tests    # lint
python -m uv run ruff format src tests   # format
python -m uv run mypy src                # type check
python -m uv run pre-commit install      # optional: run these checks before each commit
```

GitHub Actions runs lint, format, type checks, tests (90% coverage minimum), and `chronicler validate campaign` on every push.

The browser side is server-rendered HTML with HTMX, plus one small file (`src/chronicler/web/static/js/editor.js`) that runs the text editor. Every editing rule lives in Python (`edit_text.py` for the toolbar, `changes.py` for checking and saving, `forms.py` for the edit forms) and is covered by pytest. All third-party browser files are stored locally (see `src/chronicler/web/static/THIRD_PARTY.md`), so the app works offline.

## Troubleshooting
- **`uv` / `chronicler` not found:** use the `python -m uv run …` form shown above.
- **Port already in use:** `python -m uv run chronicler serve --port 8001`.
- **A page shows "Lost in the mists" in Player view:** that entry is GM-only (secret, non-canon, or a GM-only type like PCs or decisions). Switch to the GM view.
- **Asked to log in after opening a link from another site (Discord, email):** the login cookie is never sent with links from other sites, for safety. Log in and you land on the page.
- **"Too many tries":** several wrong passwords for that name; wait the number of seconds shown.
- **"The page changed on disk":** the file was edited outside the app while you were editing; see *If a page was also edited in VS Code* above.
- **Can't create an asset entry:** put its file in `campaign/assets/files/` first.
