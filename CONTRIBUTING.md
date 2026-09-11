# Contributing

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows ; source .venv/bin/activate elsewhere
pip install -r requirements.txt
pip install pytest ruff
```

## The three commands

```bash
pytest                # the test suite, ~1 s, no network and no TIDAL account needed
ruff check .          # lint
ruff check . --fix    # lint, autofixing what can be autofixed
```

CI runs exactly these two checks on Linux and Windows, Python 3.10 and 3.12.
Windows is not optional: every encoding bug this project has had appeared there
first.

## Working on a change

1. Branch off `main`: `fix/…`, `feat/…` or `docs/…`.
2. Write the test first when the change touches parsing, matching, the cache or
   any delete path. Those are the four areas that have actually broken.
3. `pytest` and `ruff check .` must pass locally before pushing.
4. Open a pull request. CI has to be green to merge.

## Tests

`tests/` mirrors the risk, not the file layout:

| File | Covers |
|---|---|
| `test_matching.py` | `norm`, `clean_title`, `clean_artist`, `score_candidate`, `dedup_key` |
| `test_parsing.py` | the JSON export from `export_apple_music.js` and the iTunes XML |
| `test_cache.py` | reading a corrupted cache, atomic writes, migration to the identity-keyed format |
| `test_destructive.py` | delete scope, retries, `verify_wipe` |
| `test_cli.py` | argument combinations that must be refused |
| `test_messages.py` | EN/FR parity, language resolution, the confirmation word |

No test may reach the network or a real TIDAL account. `tests/conftest.py`
provides the doubles; anything talking to TIDAL is a `MagicMock`.

When a bug is reported, the first commit adds a failing test that reproduces it.

## Things that must stay true

- Nothing leaves the user's machine. No telemetry, no remote server.
- Any destructive operation writes a JSON backup, asks for confirmation, then
  verifies the deletion actually happened.
- `README.md` and `README.fr.md` stay in sync; a change to one requires the other.
- No user-facing string is written inline. It goes in `messages.py`, in **both**
  `EN` and `FR`, and is printed through `t("key", **params)`. `test_messages.py`
  fails on a key present in one dictionary and not the other, on a placeholder
  lost in translation, and on a key nothing uses any more. English is the
  default; `--lang fr` (or `APPLE2TIDAL_LANG=fr`) switches.
- Never commit `.apple2tidal/`, `apple_library.json`, `*.xml` or `backup_*.json`.
  They hold OAuth tokens and a full listening history. `.gitignore` covers them —
  re-check it after any directory move.
- `tidalapi` is unofficial. Keep every call to it inside the `Tidal` class so a
  breaking change upstream stays a one-file problem.
