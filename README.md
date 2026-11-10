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

- Use a different port: `python -m uv run chronicler serve --port 8080`
- Serve a different campaign folder: `python -m uv run chronicler serve path/to/campaign`
- Try the small test world: `python -m uv run chronicler serve tests/fixtures/campaign`

The viewer reloads automatically when you save a file. Just refresh the page.

### 3. Using the viewer
- **GM / Player** (top right): switch between your full view and what players are allowed to see. Player view hides secrets, GM-only fields, and GM-only entries.
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

1. Copy the matching template from `campaign/_templates/` into the right folder (e.g. `npc.md` → `campaign/npcs/`).
2. Set a unique `id` (lowercase-with-dashes; it appears in page URLs, so don't put a hidden true name in it) and fill in what you know. Empty fields are fine.
3. Run `chronicler validate`, then check the page in the viewer.

| Thing | Syntax |
|---|---|
| Link | `[[Name]]`, `[[Alias]]`, `[[Name#Section]]`, `[[Name\|shown text]]` |
| Secret (inline) | `{secret}…{/secret}` (opens and closes on one line) |
| Secret section | `## Heading {secret}` |
| Revealed secret | `{revealed [[Session 7]]}…{/revealed}` |
| Prep note | `{prep}…{/prep}` (listed on the home page; always GM-only) |
| Image / audio | `![[img:asset-id]]`, `[[audio:asset-id]]` |
| Sublocation | `### Name` under a `## Places` heading |

Per field, the Player view shows: the `party_view` value if set, otherwise nothing if the field is secret or GM-only, otherwise the real value. Only `canon` entries are shown to players.

## Layout

| Path | What it holds |
|---|---|
| `campaign/` | The world: one Markdown file per entry, in a folder per type (`npcs/`, `locations/`, …) |
| `campaign/_templates/` | A blank, commented template for every entry type |
| `campaign/assets/` | Image/audio entries; the files themselves go in `campaign/assets/files/` |
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

## Troubleshooting
- **`uv` / `chronicler` not found:** use the `python -m uv run …` form shown above.
- **Port already in use:** `python -m uv run chronicler serve --port 8001`.
- **A page shows "Lost in the mists" in Player view:** that entry is GM-only (secret, non-canon, or a GM-only type like PCs or decisions). Switch to GM view.
