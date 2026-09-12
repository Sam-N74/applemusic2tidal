# Contributing

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows ; source .venv/bin/activate elsewhere
pip install -r requirements.txt
pip install ruff
```

Run the tool from the checkout with `python -m apple2tidal …`.

## Layout

`apple2tidal/` is a package. The engine is written once; a service is an adapter.

| Module | Role |
|---|---|
| `model.py` | `Track`, `Playlist`, `Album`, `Candidate`, `Match`: no service identifier anywhere |
| `matching.py` | scoring, `match()`, when the cache is enough |
| `albums.py` | album grouping and resolution (UPC first, matched tracks otherwise) |
| `engine.py` | `transfer()`: match, report, then write playlists, favorites, albums |
| `state.py` | `.apple2tidal/`: one `Store` per destination service, cache keyed by ISRC or normalized artist\|title |
| `providers/` | the `Source` and `Destination` protocols, plus one module per service (`apple.py`, `tidal.py`) |
| `cli.py` | argparse, confirmations, `--wipe` and `--reset` |

Adding a service means one file in `providers/` implementing `Source`, `Destination`
or both. If it needs a change in `engine.py` or `matching.py`, the contract is
wrong: fix the contract, not the caller.

## The two commands

```bash
ruff check .          # lint
ruff check . --fix    # lint, autofixing what can be autofixed
```

CI runs `ruff check .`. Nothing else gates a merge, so read your own diff:
every encoding bug this project has had appeared on Windows first.

## Working on a change

1. Branch off `main`: `fix/…`, `feat/…` or `docs/…`.
2. Take extra care with parsing, matching, the cache and any delete path.
   Those are the four areas that have actually broken.
3. `ruff check .` must pass locally before pushing.
4. Open a pull request. CI has to be green to merge.

## Checking a change by hand

There is no test suite in this repository. Before pushing, run a real transfer
against a throwaway playlist with `--dry-run`, and exercise whatever you touched:
parsing on your own export, the cache on a second run, and any delete path
against something you can afford to lose.

## Things that must stay true

- Nothing leaves the user's machine. No telemetry, no remote server.
- Any destructive operation writes a JSON backup, asks for confirmation, then
  verifies the deletion actually happened.
- `README.md` and `README.fr.md` stay in sync; a change to one requires the other.
- No user-facing string is written inline. It goes in `apple2tidal/messages.py`, in **both**
  `EN` and `FR`, and is printed through `t("key", **params)`. A key present in
  one dictionary and not the other, a placeholder lost in translation or a key
  nothing uses any more are all bugs. English is the default; `--lang fr` (or
  `APPLE2TIDAL_LANG=fr`) switches.
- Never commit `.apple2tidal/`, `apple_library.json`, `*.xml` or `backup_*.json`.
  They hold OAuth tokens and a full listening history. `.gitignore` covers them —
  re-check it after any directory move.
- `tidalapi` is unofficial. Keep every call to it inside `apple2tidal/providers/tidal.py`
  so a breaking change upstream stays a one-file problem. The engine only ever
  sees `Candidate` objects and opaque identifiers.
