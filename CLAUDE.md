# CLAUDE.md

Rules and development practices for any Claude session working in this repo. These are the user's rules. Always follow them.

- **Chronicler** is the campaign-agnostic engine in `src/chronicler/`. **Tales of Avalar** is the user's campaign, with its data in `campaign/`.
- **Read first:** `scratch/PROGRESS.md` covers status, the design spec and lore rulings. `scratch/ROADMAP.md` covers the decisions already made for later phases.

---

## 1. Working rules

### Dev cycle
1. The user asks.
2. Claude asks clarifying questions until none remain.
3. Claude presents a final plan.
4. The user approves or denies it.
5. Claude builds it, with tests.

Do not skip steps or start building before approval. Each roadmap phase follows this cycle.

### Communication
- Answer succinctly by default. The user will ask when they want more detail.

### Files
- **NEVER create a file unless the user explicitly says so in that message.** An ambiguous "yes" is NOT permission. Avoid clutter.
- Approved notes files: `scratch/PROGRESS.md`, `scratch/ROADMAP.md`, and only those `scratch/issues/*.md` files the user asks for.
- **Do not write to the Claude memory directory.** Project context lives in `scratch/` and this file.
- Files listed in an approved phase plan may be created during that phase. For anything outside the plan, ask first.
- Before changing scratch files or starting a phase, run the proposed changes by the user.
- Put throwaway scripts (generators, one-off checks) in the session scratchpad, not in the repo.

### Git
- **Claude NEVER commits or pushes.** Claude does the work and runs the tests. The user commits and pushes.

### Code reuse
- Make code reusable where possible, and avoid duplication as much as possible. Extend or extract shared helpers instead of copying logic.

### Before writing new code
- Always check first whether an existing solution can be used: existing code in this repo, a current dependency, or a better library.
- Present the options to the user, with a comparison or pro/con table that includes technical details.
- Give your recommended best practice and why.
- Keep it succinct at first. The user will ask for more explanation.

### Diagnosing and fixing
- **No guessing.** Form a hypothesis, then test it (reproduce, add logging, write a failing test) before suggesting a fix.
- Before suggesting a fix, verify that it won't collide with working functionality or tests.
- If a collision can't be avoided, present options and let the user decide how to proceed.

### Lore
- **Never guess when lore conflicts.** Flag the conflict for the user to rule on.
- Remind the user of their own lore when it is relevant.
- Record rulings in `scratch/PROGRESS.md` §6, after the user approves the change.
- Example content is structure only. Real content comes from the user's sources and rulings.
- Pasted AI brainstorms default to `status: idea` unless the user says otherwise.
- Don't invent new canon. Put open questions in the entry as `{prep}...{/prep}` notes, so they show up on the home page prep list.

---

## 2. Principles

Full detail is in `scratch/PROGRESS.md` §2 and §5.

- **North star:** a campaign info system that is pleasant to use. Usability beats features.
- **Deterministic first:** use plain code where it is nearly as useful as AI. AI is an optional suggestion layer and is never required. The exceptions the user has approved are listed in PROGRESS §2.
- **Campaign-agnostic engine:** no hardcoded Avalar names or rules in `src/`. Entry types, calendar and folders come from config.
- **Markdown + YAML frontmatter files are the source of truth.** Git provides history.
- **One owner per fact.** Databases hold only derived data (rebuildable from Markdown) or data that isn't in Markdown at all. No two-way sync.
- **Store relationships once.** The reverse direction is computed.
- Desktop only. Single editor (the user).

---

## 3. Codebase rules

- **`storage.py` (`CampaignRepository`) is the only code that reads campaign files.** Everything else goes through it.
- **Redaction is security-critical.** The Player view must never leak a secret.
  - Any change touching `redact.py`, `render.py`, `search.py`, `schema.GM_ONLY_FIELDS`, link rewriting or the viewer must keep the canary-leak tests passing.
  - New secret-bearing syntax or fields need new canary strings in `tests/fixtures/campaign/`.
  - Player-mode search must never index secret text.
- Secrets may not nest.
- Entry `id`s never change and appear in URLs. An entry with a hidden true name (`party_view.name`) needs a neutral id.
- When renaming an entry, keep the old name as an alias.
- Scores are `Decimal` with 2 places, in the range -10.00..+10.00.
- Every displayed date includes the weekday.
- Every entry type has a blank template in `campaign/_templates/`. Keep templates in sync with the schema (`tests/test_templates.py`).
- Static assets are vendored: no CDNs, and it must work offline. Record third-party licenses in `src/chronicler/web/static/THIRD_PARTY.md`. No Node. A little plain JS or Alpine.js is fine later.

---

## 4. Quality bar (before reporting work as done)

On Windows, uv is not on PATH. Run everything as `python -m uv run ...`.

```bash
python -m uv run pytest --cov                    # all tests pass; core coverage >= 90% (CI enforces)
python -m uv run ruff check src tests            # clean
python -m uv run ruff format src tests           # formatted
python -m uv run mypy src                        # strict, clean
python -m uv run chronicler validate campaign    # 0 errors (and 0 warnings unless explained)
```

- Every change ships with tests.
  - Validator: one test per error or warning type.
  - Viewer: every page is tested in both GM and Player mode (TestClient).
- After a status change on an entry, run `chronicler review`.
- After content changes, spot-check the Player view and Player search for leaks.
- Report results honestly: test counts, coverage, and validate output. If something fails or was skipped, say so.
- After a task or batch, propose the matching update to `scratch/PROGRESS.md` (status, batch log, new rulings). Write it once the user approves.

---

## 5. Layout

| Path | Holds |
|---|---|
| `src/chronicler/` | Engine: `storage`, `document`/`markup` (parsing), `schema`, `index`, `redact`, `render`, `search`, `validate`, `review`, `calendar`, `home`, `cli`, `web/` |
| `tests/` | Tests; `tests/fixtures/campaign/` is a fixture world full of canary secrets |
| `campaign/` | The world, in flat folders per type (`campaign/<type-plural>/<slug>.md`) plus `parent:` |
| `campaign/assets/` | Asset entries; the binaries live in `campaign/assets/files/` |
| `lib/source-files/` | The original source docs (tracked for now) |
| `scratch/` | Progress, roadmap and issues (the approved notes files only) |

The full syntax and run instructions are in `README.md`.
