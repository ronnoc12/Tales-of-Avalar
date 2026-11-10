# Tales of Avalar

Campaign information for *Tales of Avalar*, managed with **Chronicler**: a small engine that keeps the world in Markdown files, checks them for problems, and shows a GM or Player version in a local web viewer.

## Quick start

```bash
python -m uv sync                              # install (first time)
python -m uv run chronicler serve              # viewer at http://127.0.0.1:8000
python -m uv run chronicler validate           # check every entry for problems
python -m uv run chronicler review             # what to re-check after status changes
python -m uv run chronicler review --entry Cas # review one entry by name or alias
```

## Layout

| Path | What it holds |
|---|---|
| `campaign/` | The world: one Markdown file per entry, in a folder per type (`npcs/`, `locations/`, …) |
| `campaign/_templates/` | A blank, commented template for every entry type. Copy one to start an entry |
| `campaign/assets/` | Image/audio entries; the files themselves go in `campaign/assets/files/` |
| `campaign/calendar.yaml` | In-world calendar (months, weekdays, seasons, eras) |
| `src/chronicler/` | The engine (campaign-agnostic) |
| `tests/` | Tests, including a fixture campaign full of planted secrets |

## Writing entries

| Thing | Syntax |
|---|---|
| Link | `[[Name]]`, `[[Alias]]`, `[[Name#Section]]`, `[[Name\|shown text]]` |
| Secret (inline) | `{secret}…{/secret}` (opens and closes on one line) |
| Secret section | `## Heading {secret}` |
| Revealed secret | `{revealed [[Session 7]]}…{/revealed}` |
| Prep note | `{prep}…{/prep}` (listed on the home page) |
| Image / audio | `![[img:asset-id]]`, `[[audio:asset-id]]` |
| Sublocation | `### Name` under a `## Places` heading |

Per field, the Player view shows: the `party_view` value if set, otherwise nothing if the field is secret or GM-only, otherwise the real value. Only `canon` entries are shown to players.

## Development

```bash
python -m uv run pytest --cov      # tests + coverage
python -m uv run ruff check src tests
python -m uv run ruff format src tests
python -m uv run mypy src
python -m uv run pre-commit install   # optional: run checks before each commit
```
