# apple2tidal

**English** · [Français](README.fr.md)

Transfers playlists, library, loved tracks and albums from Apple Music to TIDAL. It runs on your machine: your library is never uploaded anywhere, there is no limit on how many tracks you move, and every track it fails to match is written down with its score.

## Why this one

**Nothing leaves your machine.** No account to create, no service to trust with your listening history. The tool reads the Apple export from your disk and talks to TIDAL directly from your computer. Web transfer services cannot work this way — their product *is* a server that reads your library. Soundiiz documents it plainly: library content is stored on Google Cloud servers in the United States.

**No limit, in one command.** A whole library moves in a single run. On the free tiers elsewhere the same library means nine manual transfers (Soundiiz stops at 200 tracks per transfer, one at a time), or is simply out of reach (TuneMyMusic caps at 500 tracks in total, FreeYourMusic at 600 tracks and one playlist).

**The transfer is verifiable.** Every track that did not make it lands in `unmatched.csv` with the best score it reached and the playlists it belonged to. The threshold is adjustable, `--dry-run` shows what would happen before anything is written, and the cache lets you replay a decision. You are not told "43 tracks not found" and left there.

**And it is reversible.** `--wipe` empties the TIDAL account again: JSON backup first, confirmation, then a check that the deletion actually happened. A botched import is not a dead end.

## What a real run looks like

1,800 tracks, 11 playlists, 97% matched exactly. That is an actual library, not a benchmark. Most matches are exact because the browser export carries the ISRC of every track and TIDAL resolves those directly; fuzzy search only handles the remainder.

The rest is written down, in `.apple2tidal/unmatched.csv`:

```csv
artist,title,album,best_score,playlists
Artist,Title (feat. Someone) [Bonus Track],Album (Expanded Edition),71.2,Road trip; Late night
```

That row is made up — the real file is your own library, which is why none of it is reproduced here. On the run above, six tracks ended up in it: two chopped-and-screwed mixtape edits, one expanded-edition bonus version that TIDAL spells differently, and three where the best candidate scored under 70, meaning nothing close enough to accept. Each one is a single line, findable by hand in a minute.

## Why not Soundiiz

Soundiiz, TuneMyMusic and FreeYourMusic do several things better, and it is worth saying so:

- **20+ platforms.** This tool does Apple Music → TIDAL, and nothing else.
- **Scheduled synchronisation**, on their paid tiers. This is a one-time import: run it again and it will not redo the work, but it will not watch Apple for changes either.
- **Zero installation.** They run in a browser tab. Here you need Python, and a console window for the export.

What they cannot offer:

| | apple2tidal | Soundiiz | TuneMyMusic | FreeYourMusic |
|---|---|---|---|---|
| Where your library goes | stays on your disk | Google Cloud servers, United States | their servers | their servers |
| Free limit | none | 200 tracks per transfer, one at a time; albums, artists and loved tracks are premium-only | 500 tracks in total | 600 tracks, one playlist |
| Paid tier | — | ~€39/year | ~$24/year | ~$5, then subscriptions |
| Report of what failed | `unmatched.csv`, scored, per playlist | a count | a count | a count |

If you are moving 200 tracks once, use Soundiiz — it is faster. If you are moving a library you care about, or you are leaving Apple precisely because you would rather not hand your listening history to another platform, this one was written for that.

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Export the Apple Music library

### Option A — from the browser (recommended, provides ISRCs)

1. Open https://music.apple.com and sign in.
2. Press F12 → **Console** tab. If Chrome shows a warning, type `allow pasting` and press Enter.
3. Paste the contents of `export_apple_music.js` and press Enter.
4. Wait for the `[export] …` logs; `apple_library.json` downloads at the end.

The JSON contains tracks, playlists, loved tracks, albums, and the ISRC of every track linked to the catalog. TIDAL can resolve those directly (`get_tracks_by_isrc`), so fuzzy search is only a fallback.

### Option B — from the Music app (Mac) / iTunes (Windows)

**File → Library → Export Library…** → `Library.xml`. This format carries no ISRC, so matching is fuzzy only.

## 3. Run

```bash
# Recommended first step: matching only, nothing is written to TIDAL
python apple2tidal.py apple_library.json --dry-run

# Then, as needed:
python apple2tidal.py apple_library.json --playlists              # recreate playlists
python apple2tidal.py apple_library.json --favorites              # whole library → favorite tracks
python apple2tidal.py apple_library.json --loved                  # only Apple "loved" tracks → favorites
python apple2tidal.py apple_library.json --albums                 # complete albums → favorite albums
python apple2tidal.py apple_library.json --all                    # everything
```

On first run a `link.tidal.com/XXXXX` link is printed: open it, sign in, and the script continues on its own. The session is stored in `.apple2tidal/tidal_session.json`.

## Options

| Option | Effect |
|---|---|
| `--only "Name"` | Process only this playlist (repeatable) |
| `--skip-smart` | Ignore smart playlists |
| `--overwrite` | Clear and refill an existing TIDAL playlist of the same name (otherwise it is skipped). If two TIDAL playlists share that name, neither is touched |
| `--threshold 85` | Minimum match score (default 78). Higher means fewer false positives and more unmatched tracks. Cached matches that no longer clear the new threshold are searched again |
| `--rematch` | Search again for the tracks that stayed unmatched at the same threshold |
| `--workers 8` | Parallel TIDAL requests (default 8) |
| `--delay 0.5` | Pause between requests; add only if TIDAL rate-limits |
| `--reset` | **Destructive**: empties the TIDAL account before importing (see below) |
| `--reset-scope all` | Makes `--reset` delete *all* playlists, not only those matching an Apple playlist name |
| `--yes` | Skip the `--reset` confirmation prompt |
| `--lang fr` | Output language: `en` (default) or `fr`. Also read from the `APPLE2TIDAL_LANG` environment variable. The deletion confirmation word follows the language: `DELETE` in English, `SUPPRIMER` in French |

## Emptying the TIDAL account without reimporting (`--wipe`)

```bash
python apple2tidal.py --wipe --dry-run   # list what would be deleted
python apple2tidal.py --wipe             # ask for confirmation, delete, stop
```

Deletes playlists, favorite tracks, favorite albums and favorite artists, and unfollows followed playlists (`--keep-followed` preserves them). Nothing is imported afterwards: the account stays empty.

The export file is not required. A JSON backup is written before any deletion, as with `--reset`.

**`--wipe` is not `--reset`**: `--reset` empties *and then reimports* in the same command, so the account ends up mirroring the Apple export. `--wipe` is the one to use for a blank account.

## Starting over (`--reset`)

```bash
python apple2tidal.py apple_library.json --all --reset --dry-run   # show what would be deleted
python apple2tidal.py apple_library.json --all --reset             # confirm, then delete + reimport
```

What `--reset` deletes depends on the requested actions:
- with `--playlists`: playlists owned by the account (playlists created by other users and merely followed are left alone)
- with `--favorites` / `--loved`: all favorite tracks
- with `--albums`: all favorite albums

By default (`--reset-scope imported`), only playlists whose name matches a playlist in the Apple export are deleted, so playlists created directly on TIDAL survive. `--reset-scope all` deletes every owned playlist.

Before any deletion, the state of the account (playlists with their tracks, favorites, albums) is written to `.apple2tidal/backup_YYYYMMDD_HHMMSS.json`. It is a readable backup, not an undo button: TIDAL has no trash, and a deleted playlist is gone for good.

## How matching works

A track with an ISRC (JSON export) is resolved directly. Otherwise it is searched on TIDAL through several queries (artist + cleaned title, title alone, and so on) and candidates are scored:
- title 55%, artist 35%, album 10% (fuzzy, accent- and case-insensitive, with `feat.`, `Remastered` and similar suffixes stripped)
- a penalty when durations differ by more than 5 s, a heavy penalty beyond 20 s

Results are cached in `.apple2tidal/matches.json`, so an interrupted run resumes where it stopped. Each entry records the threshold it was decided under, so changing `--threshold` re-evaluates what it should. `--albums` caches its UPC lookups there too, so a second run costs no request at all. Unmatched tracks are listed in `.apple2tidal/unmatched.csv` together with the playlists they belong to, for manual handling.

## Speed

Three factors matter:

- **Parallelism**: `--workers 8` by default. `--workers 16` is roughly twice as fast; if `[rate-limit] pause Xs` messages appear, go back down to 4–6 (or add `--delay 0.2`), otherwise more time is spent backing off than requesting.
- **Deduplication**: a track present in five playlists costs a single lookup. Two entries count as identical when they share an ISRC, or failing that a normalized artist and title.
- **Cache**: `.apple2tidal/matches.json`. A second run performs no search at all. Do not delete it.

On the write side, tracks already in favorites are detected and skipped, and `--reset` deletions run in parallel.

## Files that must stay local

Nothing is sent anywhere, but the files the tool writes are your library in plain text. The repository contains code only, no secrets, and every file below is covered by the bundled `.gitignore`:

| File | Why |
|---|---|
| `.apple2tidal/tidal_session.json` | TIDAL OAuth tokens. Anyone who obtains it gets account access without a password. |
| `.apple2tidal/backup_*.json` | A full dump of the TIDAL account. |
| `apple_library.json`, `*.xml` | The entire Apple library (tracks, playlists, ISRCs). |
| `.apple2tidal/matches.json`, `unmatched.csv` | Listening history in plain text. |

If one of them has already been committed, `git rm --cached` is not enough: the file remains in history. The history has to be rewritten (`git filter-repo`) **and** the TIDAL session revoked from the account settings.

`export_apple_music.js` reads the `media-user-token` cookie and the Apple token embedded in the page to call the API. It uses the existing session, in the browser, and sends nothing anywhere else. As with any console snippet, read it before running it.

## Limitations

- `tidalapi` is unofficial: a change on TIDAL's side can break the tool at any time.
- Apple playlists are not kept in sync afterwards; this is a one-time import.
- Tracks exclusive to Apple, or listed under a different name on TIDAL, end up in `unmatched.csv`.
- Parallelism relies on the `tidalapi` HTTP session; beyond roughly 16 workers the main effect is TIDAL rate-limiting.
- Playlist folders are not recreated (the playlists inside are, flattened).

## License

MIT. Free, and it will stay free — the "nothing leaves your machine" promise is only worth something if it still holds next year.

If it saved you an afternoon, say so in an issue. That is enough.
