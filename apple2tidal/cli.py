"""
apple2tidal — transfère une bibliothèque musicale d'un service vers un autre.

Sources      : export Apple Music (.xml de l'app Musique, ou .json de
               export_apple_music.js, qui porte les ISRC), ou Spotify via son API.
Destinations : TIDAL via la lib non officielle `tidalapi`, ou Spotify.
Défaut       : Apple Music → TIDAL, comme avant l'arrivée de --from et --to.

Usage rapide :
    apple2tidal Bibliothèque.xml --dry-run          # analyse + matching, rien n'est écrit
    apple2tidal Bibliothèque.xml --playlists         # crée les playlists
    apple2tidal Bibliothèque.xml --favorites         # toute la bibliothèque en favoris
    apple2tidal Bibliothèque.xml --loved --albums    # titres aimés + albums complets
    apple2tidal Bibliothèque.xml --all               # tout
    apple2tidal --from spotify --all                 # sans fichier : tout vient de l'API

Le matching est mis en cache sous .apple2tidal/<destination>/matches.json, indexé
sur l'ISRC : relancer = reprendre, et changer de source ne le périme pas.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import state
from .engine import Options, transfer
from .matching import DEFAULT_THRESHOLD
from .messages import LANGUAGES, resolve_lang, set_lang, t
from .providers.apple import AppleExport
from .providers.spotify import Spotify
from .providers.tidal import Tidal
from .state import Store

SOURCES = ("apple", "spotify")
DESTINATIONS = ("spotify", "tidal")


def build_source(args):
    """La source demandee. Apple se lit dans un fichier, les autres par leur API."""
    if args.source == "apple":
        return AppleExport(args.library)
    return Spotify(Store(state.STATE_DIR / args.source), dry_run=args.dry_run,
                   delay=args.delay, workers=args.workers)


def build_destination(args, store: Store):
    """La destination demandee. C'est elle qui porte le cache des matchs."""
    if args.destination == "spotify":
        return Spotify(store, dry_run=args.dry_run, delay=args.delay, workers=args.workers)
    return Tidal(store, dry_run=args.dry_run, delay=args.delay, workers=args.workers)


def confirmed() -> bool:
    """Demande le mot de confirmation, dans la langue courante (DELETE / SUPPRIMER)."""
    word = t("confirm.word")
    return input(t("confirm.prompt", word=word)).strip() == word


def main():
    # Windows : console/fichiers en UTF-8, et on coupe le bruit "Track 'x' is unavailable" de tidalapi
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.getLogger("tidalapi").setLevel(logging.ERROR)

    # --lang est lu avant argparse : les textes d'aide doivent déjà être traduits
    # quand on les déclare. argparse revalide ensuite la valeur via `choices`.
    lang = set_lang(resolve_lang())

    ap = argparse.ArgumentParser(prog="apple2tidal", description=t("cli.description"))
    ap.add_argument("library", type=Path, nargs="?", help=t("cli.help.library"))
    ap.add_argument("--from", dest="source", default="apple", choices=SOURCES,
                    help=t("cli.help.source"))
    ap.add_argument("--to", dest="destination", default="tidal", choices=DESTINATIONS,
                    help=t("cli.help.destination"))
    ap.add_argument("--playlists", action="store_true", help=t("cli.help.playlists"))
    ap.add_argument("--favorites", action="store_true", help=t("cli.help.favorites"))
    ap.add_argument("--loved", action="store_true", help=t("cli.help.loved"))
    ap.add_argument("--albums", action="store_true", help=t("cli.help.albums"))
    ap.add_argument("--all", action="store_true", help=t("cli.help.all"))
    ap.add_argument("--only", action="append", default=[], help=t("cli.help.only"))
    ap.add_argument("--skip-smart", action="store_true", help=t("cli.help.skip_smart"))
    ap.add_argument("--overwrite", action="store_true", help=t("cli.help.overwrite"))
    ap.add_argument("--wipe", action="store_true", help=t("cli.help.wipe"))
    ap.add_argument("--keep-followed", action="store_true", help=t("cli.help.keep_followed"))
    ap.add_argument("--reset", action="store_true", help=t("cli.help.reset"))
    ap.add_argument("--reset-scope", default="imported", choices=["imported", "all"],
                    help=t("cli.help.reset_scope"))
    ap.add_argument("--yes", action="store_true", help=t("cli.help.yes"))
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=t("cli.help.threshold", default=DEFAULT_THRESHOLD))
    ap.add_argument("--rematch", action="store_true", help=t("cli.help.rematch"))
    ap.add_argument("--dry-run", action="store_true", help=t("cli.help.dry_run"))
    ap.add_argument("--delay", type=float, default=0.0, help=t("cli.help.delay"))
    ap.add_argument("--workers", type=int, default=8, help=t("cli.help.workers"))
    ap.add_argument("--lang", default=lang, choices=sorted(LANGUAGES),
                    help=t("cli.help.lang"))
    args = ap.parse_args()

    if args.wipe and (args.all or args.playlists or args.favorites or args.loved
                      or args.albums or args.reset):
        ap.error(t("cli.error.wipe_with_import"))
    if args.source == args.destination:
        ap.error(t("cli.error.same_service", service=args.source))
    if args.source == "apple" and not args.wipe and args.library is None:
        ap.error(t("cli.error.library_missing"))
    if args.all:
        args.playlists = args.favorites = args.albums = True
    if args.reset and not (args.playlists or args.favorites or args.loved or args.albums):
        ap.error(t("cli.error.reset_needs_action"))
    if not (args.wipe or args.playlists or args.favorites or args.loved
            or args.albums or args.dry_run):
        ap.error(t("cli.error.no_action"))

    store = Store(state.STATE_DIR / args.destination)

    # ---- Mode --wipe : vider le compte, puis s'arrêter
    if args.wipe:
        dest = build_destination(args, store)
        if args.dry_run:
            print("\n" + t("main.dry_run_notice", service=dest.name))
        print(t("main.reading_account", service=dest.name))
        snap = dest.snapshot(workers=args.workers)
        own = [p for p in snap["playlists"] if p["own"]]
        foll = snap.get("followed_playlists", [])
        print(t("main.backup_written", path=snap["path"]))
        print(t("wipe.summary", playlists=len(own),
                tracks=len(snap["favorite_tracks"]),
                albums=len(snap["favorite_albums"]),
                artists=len(snap.get("favorite_artists", [])))
              + ("" if args.keep_followed else t("wipe.summary_followed", n=len(foll))))
        for p in own:
            print(t("main.playlist_line", name=p["name"], n=p["num_tracks"]))
        if not args.dry_run and not args.yes:
            print("\n" + t("confirm.wipe_warning"))
            if not confirmed():
                sys.exit(t("confirm.cancelled"))
        dest.wipe(snap, playlists=True, favorites=True, albums=True, only_names=None,
                   artists=True, followed=not args.keep_followed)
        ok = dest.verify_wipe(snap, playlists=True, favorites=True, albums=True)
        print("\n" + (t("main.done") if ok else t("main.done_with_errors")))
        return

    source = build_source(args)
    lib = source.read()
    n_isrc = sum(1 for a in lib.tracks.values() if a.isrc)
    if args.skip_smart:
        lib.playlists = [p for p in lib.playlists if not p.smart]
    if args.only:
        wanted = {n.lower() for n in args.only}
        lib.playlists = [p for p in lib.playlists if p.name.lower() in wanted]
    print(t("source.summary", service=source.name, tracks=len(lib.tracks), isrc=n_isrc, playlists=len(lib.playlists)))
    for p in lib.playlists:
        print(t("source.playlist_line", name=p.name, n=len(p.track_ids),
                smart=t("source.smart_tag") if p.smart else ""))

    dest = build_destination(args, store)

    # ---- Reset du compte TIDAL (avant tout import)
    if args.reset:
        if args.dry_run:
            print("\n" + t("main.dry_run_notice", service=dest.name))
        print("\n" + t("main.current_account", service=dest.name))
        snap = dest.snapshot(workers=args.workers)
        own = [p for p in snap["playlists"] if p["own"]]
        only = {p.name for p in lib.playlists} if args.reset_scope == "imported" else None
        to_del = [p for p in own if only is None or p["name"] in only]
        n_fav = len(snap["favorite_tracks"]) if (args.favorites or args.loved) else 0
        n_alb = len(snap["favorite_albums"]) if args.albums else 0
        print(t("main.backup_written", path=snap["path"]))
        print(t("reset.summary", to_delete=len(to_del), own=len(own),
                tracks=n_fav, albums=n_alb))
        for p in to_del:
            print(t("main.playlist_line", name=p["name"], n=p["num_tracks"]))
        if not args.dry_run and not args.yes:
            print("\n" + t("confirm.reset_warning"))
            if not confirmed():
                sys.exit(t("confirm.cancelled"))
        dest.wipe(snap, playlists=args.playlists, favorites=(args.favorites or args.loved),
                   albums=args.albums, only_names=only)
        if not dest.verify_wipe(snap, playlists=args.playlists,
                                 favorites=(args.favorites or args.loved),
                                 albums=args.albums, only_names=only):
            sys.exit(t("main.import_cancelled"))

    transfer(lib, dest, store, Options(
        playlists=args.playlists, favorites=args.favorites, loved=args.loved,
        albums=args.albums, overwrite=args.overwrite, threshold=args.threshold,
        rematch=args.rematch, workers=args.workers, dry_run=args.dry_run,
    ))
    print("\n" + t("main.done"))


if __name__ == "__main__":
    main()
