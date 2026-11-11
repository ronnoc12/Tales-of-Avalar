# Tales of Avalar

Campaign information for *Tales of Avalar*, managed with **Chronicler**: a small engine that keeps the world in Markdown files, checks them for problems, and shows a GM or Player version in a local web viewer.

## Running it

### 1. One-time setup
You need **Python 3.13+** and **git**.

```bash
python -m pip install --user uv   # install the uv package manager
python -m uv sync                 # create .venv and install Chronicler + dependencies
```

> `uv` is run as `python -m uv` because the pip-installed `uv` command may not be on your PATH. If `uv` works on its own in your terminal, you can drop the `python -m` prefix everywhere.

### 2. Start the viewer
From the project folder:

```bash
python -m uv run chronicler serve
```

Then open **http://127.0.0.1:8000** in your browser. Stop the server with **Ctrl+C**.

**First run:** the browser asks you to create the **GM account** (full access: GM view, editing, every tool). Add player accounts afterwards on the **Users** page (your name, top right → Users). Players only ever see the Player view and cannot edit; the server enforces this on every page.

Forgot the password? From the project folder:

```bash
python -m uv run chronicler user reset-password "Your Name"
python -m uv run chronicler user add "Jo" --role player   # or --role gm
python -m uv run chronicler user list
```

Accounts live in `campaign/_data/chronicler.db` (git-ignored, never committed).

- Use a different port: `python -m uv run chronicler serve --port 8080`
- Serve a different campaign folder: `python -m uv run chronicler serve path/to/campaign`
- Try the small test world: `python -m uv run chronicler serve tests/fixtures/campaign`

The viewer reloads automatically when you save a file. Just refresh the page.

### 3. Using the viewer
- **GM / Player** (top right, GM accounts only): switch between your full view and what players are allowed to see. Player view hides secrets, GM-only fields, and GM-only entries.
- **☾ / ☀**: dark or light parchment theme. **Aa**: cycle fonts (default, OpenDyslexic, Atkinson Hyperlegible). Both are remembered per browser.
- **Search** (top bar): finds every reference to a name or keyword, with snippets; misspellings get "did you mean" suggestions.
- **Home page:** last session summary, spotlight (which players are due a moment), what's coming up this week in-world, hanging threads, and session prep notes (click one to jump to it).
- **Sidebar tools (GM only):** *Party beliefs vs truth* and *Issues* (same checks as `validate`).

### 4. Check the world for problems
```bash
python -m uv run chronicler validate                 # all errors and warnings
python -m uv run chronicler validate --errors-only   # errors only
```
It reports broken links (with spelling suggestions), bad tags, bad fields or dates, duplicate names, and places where player-visible text would reveal a secret. Run it after editing; it exits non-zero if there are errors.

### 5. After changing an entry's status
```bash
python -m uv run chronicler review               # every status change since your last commit
python -m uv run chronicler review --entry Cas   # one entry, by name, alias, or id
```
Lists everything that links to or mentions the changed entry, its alternate versions, and canon entries still pointing at something retired.

## Editing the world

### In the browser (GM view)
- **Quick edit:** hover a field on an entry page and click **✎** to change just that field (dropdowns for fixed lists, number boxes for scores, name suggestions for links). It saves straight to the page.
- **Edit mode:** **Edit** on any entry page. Every field gets a widget generated from the schema, plus the text editor. Your work **autosaves as a draft** 2 seconds after you stop typing (and at least every 10 seconds while you type), only when something changed. **Save** writes it to the page; problems that block saving are listed with buttons to fix them, and your work stays in the draft. Ctrl+S saves.
- **Toolbar:** select text, then **Secret** / **Prep** (one continuous tag; touching tags merge, a partly covered tag is pulled inside), **Unmark** (text outside the selection keeps the tag), **Reveal** (turns the secret at the cursor into revealed, recording the session), **Link**, **Image**, **Section secret**.
- **Rename** (keep the old name as an alias, or rewrite every link), **Retire** (page stays, marked retired, hidden from players), **Delete** (shows what links to it and warns about uncommitted changes), **+ New entry** (from the type's template).
- **Leaving with unsaved edits** asks: Save now, Keep in draft, or Discard. Unsaved drafts are listed on the home page.
- **Edited the same file in VS Code meanwhile?** Saving won't overwrite it: your edits go to `campaign/_drafts/conflicts/<id>-<date>.md` and the editor reloads the file. Drafts live in `campaign/_drafts/` (git-ignored) in the same format as entries.
- Only the GM edits. A save never changes anything you didn't touch (formatting, comments, and key order are kept).

### By hand
1. Copy the matching template from `campaign/_templates/` into the right folder (e.g. `npc.md` → `campaign/npcs/`).
2. Set a unique `id` (lowercase-with-dashes; it appears in page URLs, so don't put a hidden true name in it) and fill in what you know. Empty fields are fine. Put text with commas in "quotes" inside `{ ... }` items.
3. Run `chronicler validate`, then check the page in the viewer.

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
| `campaign/_templates/` | A blank, commented template for every entry type |
| `campaign/assets/` | Image/audio entries; the files themselves go in `campaign/assets/files/` (put the file there before creating its asset entry) |
| `campaign/_drafts/`, `campaign/_data/` | Editor drafts and conflict copies; the accounts database. Both git-ignored |
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

The browser side is HTMX plus one small file (`src/chronicler/web/static/js/editor.js`) for the text editor; every editing rule lives in Python (`edit_text.py`, `changes.py`, `forms.py`) and is covered by pytest.

## Troubleshooting
- **`uv` / `chronicler` not found:** use the `python -m uv run …` form shown above.
- **Port already in use:** `python -m uv run chronicler serve --port 8001`.
- **A page shows "Lost in the mists" in Player view:** that entry is GM-only (secret, non-canon, or a GM-only type like PCs or decisions). Switch to GM view.
