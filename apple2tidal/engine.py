"""Le transfert lui-meme : matcher, rapporter, puis ecrire playlists, favoris et albums.

Ecrit une fois, pour n'importe quel couple (source, destination) qui respecte le
contrat de `providers`. La ligne de commande et, plus tard, l'interface ne font
que le parametrer.
"""

from __future__ import annotations

import csv
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from . import state
from .albums import cached_upc_lookup, guess_albums, resolve_albums
from .matching import DEFAULT_THRESHOLD, match, needs_match
from .messages import t
from .model import Library, Track, dedup_key
from .providers import Destination
from .state import Store, migrate_cache


@dataclass
class Options:
    playlists: bool = False
    favorites: bool = False
    loved: bool = False
    albums: bool = False
    overwrite: bool = False
    threshold: float = DEFAULT_THRESHOLD
    rematch: bool = False
    workers: int = 8
    dry_run: bool = False


def select_tracks(lib: Library, opts: Options) -> set[str]:
    """Cles des titres qu'il faut matcher pour les actions demandees."""
    needed: set[str] = set()
    if opts.playlists:
        for p in lib.playlists:
            needed.update(p.track_ids)
    if opts.favorites or opts.albums:
        needed.update(lib.tracks)
    if opts.loved:
        needed.update(k for k, a in lib.tracks.items() if a.loved)
    if opts.dry_run and not needed:
        needed.update(lib.tracks)
    return needed


def match_tracks(lib: Library, needed: set[str], dest: Destination, store: Store,
                 opts: Options) -> dict[str, dict]:
    """Matche les titres demandes chez la destination, en s'appuyant sur le cache.
    Renvoie le cache, mis a jour et ecrit sur disque."""
    cache = migrate_cache(store.load_cache(), lib.tracks)

    # Un seul lookup par titre distinct : les doublons entre playlists sont gratuits
    groups: dict[str, Track] = {}
    for tid in needed:
        groups.setdefault(dedup_key(lib.tracks[tid]), lib.tracks[tid])
    todo = [k for k in groups if needs_match(cache.get(k), opts.threshold, opts.rematch)]
    stale = sum(1 for k in todo if k in cache)
    if stale:
        print(t("cache.threshold_changed", n=stale, threshold=opts.threshold))
    print(t("match.plan", needed=len(needed), groups=len(groups), todo=len(todo),
            cached=len(groups) - len(todo)))

    t0 = time.time()
    lock = threading.Lock()
    counter = [0]

    def work(key: str):
        a = groups[key]
        m = match(a, dest, opts.threshold)
        with lock:
            cache[key] = asdict(m)
            counter[0] += 1
            i = counter[0]
            flag = "✓" if m.tidal_id else "✗"
            print(t("match.line", i=i, total=len(todo), flag=flag,
                    artist=a.artist, name=a.name, score=m.score)
                  + (t("match.line_target", tidal_artist=m.tidal_artist,
                       tidal_title=m.tidal_title) if m.tidal_id else ""))
            if i % 50 == 0:
                store.save_cache(cache)

    if todo:
        with ThreadPoolExecutor(max_workers=opts.workers) as ex:
            for f in as_completed([ex.submit(work, k) for k in todo]):
                try:
                    f.result()
                except Exception as e:  # noqa: BLE001
                    print(t("match.error", error=e))
    store.save_cache(cache)
    if todo:
        rate = len(todo) / max(time.time() - t0, 0.001)
        print(t("match.finished", seconds=time.time() - t0, rate=rate))
    return cache


def write_report(lib: Library, needed: set[str], cache: dict, path: Path) -> int:
    """Ecrit le CSV des titres non trouves. Renvoie leur nombre."""
    unmatched = {tid: lib.tracks[tid] for tid in needed
                 if not cache.get(dedup_key(lib.tracks[tid]), {}).get("tidal_id")}
    pl_of = defaultdict(list)
    for p in lib.playlists:
        for tid in p.track_ids:
            pl_of[tid].append(p.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["artist", "title", "album", "best_score", "playlists"])
        for tid, a in sorted(unmatched.items(), key=lambda kv: (kv[1].artist.lower(), kv[1].name.lower())):
            w.writerow([a.artist, a.name, a.album,
                        cache.get(dedup_key(a), {}).get("score", 0),
                        "; ".join(pl_of.get(tid, []))])
    print(t("match.report", found=len(needed) - len(unmatched), total=len(needed), path=path))
    return len(unmatched)


def push_playlists(lib: Library, dest: Destination, cache: dict, opts: Options) -> None:
    print("\n" + t("section.playlists"))
    existing = {} if opts.dry_run else dest.existing_playlists()
    for p in lib.playlists:
        ids, seen = [], set()
        for tid in p.track_ids:
            x = cache.get(dedup_key(lib.tracks[tid]), {}).get("tidal_id")
            if x and x not in seen:
                ids.append(x)
                seen.add(x)
        if not ids:
            print(t("playlist.no_match", name=p.name))
            continue
        miss = len(p.track_ids) - len(ids)
        desc = t("playlist.description") + (t("playlist.description_missing", n=miss)
                                            if miss else "")
        dest.create_playlist(p.name, desc, ids, existing, opts.overwrite)


def push_favorites(lib: Library, dest: Destination, cache: dict, opts: Options) -> None:
    print("\n" + t("section.favorites"))
    src = lib.tracks.values() if opts.favorites else (a for a in lib.tracks.values() if a.loved)
    ids = list(dict.fromkeys(x for x in (cache.get(dedup_key(a), {}).get("tidal_id") for a in src) if x))
    dest.favorite_tracks(ids)


def push_albums(lib: Library, dest: Destination, cache: dict, store: Store, opts: Options) -> None:
    print("\n" + t("section.albums"))
    if lib.albums:
        upcs = {al.upc for al in lib.albums if al.upc}
        todo_upc = {u for u in upcs if "upc:" + u not in cache}
        if upcs:
            print(t("albums.resolving", n=len(todo_upc), cached=len(upcs) - len(todo_upc)))
        found = resolve_albums(lib.albums, lib.tracks, cache,
                               cached_upc_lookup(cache, dest.album_by_upc, opts.rematch),
                               workers=opts.workers)
        store.save_cache(cache)
    else:
        print(t("albums.no_declared_list"))
        found = guess_albums(lib.tracks, cache)
    for al, _, source in found:
        print(t("albums.line", artist=al.artist, name=al.name,
                source=t("albums.source_upc") if source == "upc"
                else t("albums.source_tracks")))
    print(t("albums.detected", n=len(found)))
    dest.favorite_albums([album_id for _, album_id, _ in found])


def transfer(lib: Library, dest: Destination, store: Store, opts: Options) -> dict[str, dict]:
    """Un transfert complet. Renvoie le cache des matchs."""
    needed = select_tracks(lib, opts)
    cache = match_tracks(lib, needed, dest, store, opts)
    write_report(lib, needed, cache, state.REPORT_FILE)
    if opts.playlists:
        push_playlists(lib, dest, cache, opts)
    if opts.favorites or opts.loved:
        push_favorites(lib, dest, cache, opts)
    if opts.albums:
        push_albums(lib, dest, cache, store, opts)
    return cache
