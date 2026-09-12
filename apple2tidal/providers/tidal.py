"""Destination TIDAL, via la lib non officielle `tidalapi` (login OAuth navigateur).

Seul module qui importe tidalapi : une rupture en amont reste un probleme d'un
seul fichier. Les objets tidalapi ne sortent jamais d'ici — le moteur recoit des
`Candidate` et des identifiants.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import state
from ..messages import t
from ..model import Candidate
from ..state import Store

try:
    import tidalapi
except ImportError:
    sys.exit("pip install tidalapi rapidfuzz")

# Etat ecrit a plat par les versions d'avant le sous-dossier par service.
LEGACY_FILES = {"tidal_session.json": "session.json", "matches.json": "matches.json"}


def _candidate(track: tidalapi.Track) -> Candidate:
    return Candidate(
        id=track.id, name=track.name,
        artists=[x.name for x in (track.artists or [track.artist]) if x],
        album=track.album.name if track.album else "",
        album_id=track.album.id if track.album else None,
        duration_s=track.duration, popularity=track.popularity or 0,
    )


class Tidal:
    def __init__(self, store: Store, dry_run: bool, delay: float = 0.0, workers: int = 8):
        self.store = store
        self.dry = dry_run
        self.delay = delay
        self.workers = workers
        self._lock = threading.Lock()
        self._pause_until = 0.0  # backoff global partagé entre les threads
        store.adopt(state.STATE_DIR, LEGACY_FILES)
        store.dir.mkdir(parents=True, exist_ok=True)
        self.session = tidalapi.Session()
        ok = self.session.login_session_file(store.session_file)
        if not ok or not self.session.check_login():
            sys.exit(t("tidal.login_failed"))
        user = self.session.user
        print(t("tidal.connected", user=user.username if hasattr(user, "username") else user.id))

    # -- retry générique sur 429 / erreurs réseau, sûr en multi-thread
    def _call(self, fn, *a, retries: int = 5, **kw):
        for i in range(retries):
            # respecte un éventuel backoff déclenché par un autre thread
            wait_for = self._pause_until - time.monotonic()
            if wait_for > 0:
                time.sleep(wait_for)
            try:
                r = fn(*a, **kw)
                if self.delay:
                    time.sleep(self.delay)
                return r
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "429" in msg or "Too Many" in msg or "timed out" in msg.lower():
                    wait = 2 ** i * 2
                    with self._lock:
                        if self._pause_until - time.monotonic() < wait:
                            self._pause_until = time.monotonic() + wait
                            print(t("net.rate_limit_pause", seconds=wait))
                    continue
                if i == retries - 1:
                    raise
                time.sleep(1)
        return None

    # -- catalogue
    def tracks_by_isrc(self, isrc: str) -> list[Candidate]:
        try:
            found = self._call(self.session.get_tracks_by_isrc, isrc)
        except Exception:  # noqa: BLE001
            return []
        return [_candidate(c) for c in found if c.available]

    def search_tracks(self, query: str, limit: int = 12) -> list[Candidate]:
        try:
            res = self._call(self.session.search, query, models=[tidalapi.Track], limit=limit)
        except Exception as e:  # noqa: BLE001
            print(t("search.error", query=query, error=e))
            return []
        found = res.get("tracks", []) if isinstance(res, dict) else getattr(res, "tracks", [])
        return [_candidate(c) for c in found if c.available]

    def album_by_upc(self, upc: str) -> int | None:
        """Album TIDAL portant cet UPC, ou None.

        tidalapi leve quand le catalogue ne contient pas l'UPC. L'absence est
        traduite en liste vide *avant* d'atteindre `_call`, qui retente toute
        exception : sinon chaque album manquant coute cinq requetes et quatre
        secondes d'attente, et une bibliotheque en compte des centaines.
        """
        def lookup():
            try:
                return self.session.get_albums_by_barcode(upc)
            except (tidalapi.exceptions.ObjectNotFound,
                    tidalapi.exceptions.InvalidUPC):
                return []

        try:
            albums = self._call(lookup)
        except Exception:  # noqa: BLE001
            return None
        return albums[0].id if albums else None

    def _call_ok(self, fn, *a, **kw) -> bool:
        """Comme _call, mais traite un retour False comme un échec (tidalapi
        renvoie booléen sur delete/remove au lieu de lever une exception)."""
        r = self._call(fn, *a, **kw)
        if r is False:
            raise RuntimeError(t("tidal.api_refused"))
        return True

    def _parallel(self, fn, items: list, workers: int, label: str) -> int:
        """Applique fn à chaque item en parallèle. Renvoie le nombre de succès."""
        if not items:
            return 0
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self._call_ok, fn, str(x)): x for x in items}
            for f in as_completed(futs):
                try:
                    f.result()
                    done += 1
                except Exception as e:  # noqa: BLE001
                    print(t("parallel.item_error", label=label, item=futs[f], error=e))
                print(t("parallel.progress", label=label, done=done, total=len(items)), end="\r")
        print(t("parallel.done", label=label, done=done, total=len(items)))
        return done

    # -- lecture complète (pagination)
    def _paginate(self, fn, page: int = 50) -> list:
        out, offset = [], 0
        while True:
            batch = self._call(fn, limit=page, offset=offset)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < page:
                break
            offset += page
        return out

    def snapshot(self, workers: int = 8) -> dict:
        """Sauvegarde lisible de l'état actuel du compte, avant toute suppression."""
        fav = self.session.user.favorites
        pls = self._call(self.session.user.playlists)
        # seules les UserPlaylist (créées par le compte connecté) sont supprimables
        self._pl_objects = {p.id: p for p in pls}
        tracks_of: dict = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self._call, p.tracks, limit=100): p.id for p in pls}
            for f in as_completed(futs):
                try:
                    tracks_of[futs[f]] = f.result() or []
                except Exception:  # noqa: BLE001
                    tracks_of[futs[f]] = []
        data = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "playlists": [
                {"id": p.id, "name": p.name, "num_tracks": p.num_tracks,
                 "url": getattr(p, "share_url", None),
                 "own": isinstance(p, tidalapi.playlist.UserPlaylist),
                 "tracks": [{"id": t.id, "name": t.name,
                             "artist": t.artist.name if t.artist else ""}
                            for t in tracks_of[p.id]]}
                for p in pls
            ],
            "favorite_tracks": [{"id": t.id, "name": t.name,
                                 "artist": t.artist.name if t.artist else ""}
                                for t in self._paginate(fav.tracks)],
            "favorite_albums": [{"id": a.id, "name": a.name,
                                 "artist": a.artist.name if a.artist else ""}
                                for a in self._paginate(fav.albums)],
            "favorite_artists": [{"id": a.id, "name": a.name}
                                 for a in self._paginate(fav.artists)],
            "followed_playlists": [{"id": p.id, "name": p.name}
                                   for p in self._paginate(fav.playlists)
                                   if p.id not in self._pl_objects],
        }
        path = self.store.backup_path()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"path": path, **data}

    def wipe(self, snap: dict, playlists: bool, favorites: bool, albums: bool,
             only_names: set[str] | None = None, artists: bool = False,
             followed: bool = False):
        """Supprime playlists / favoris. only_names : ne toucher que ces playlists."""
        if playlists:
            targets = [p for p in snap["playlists"]
                       if p["own"] and (only_names is None or p["name"] in only_names)]
            for p in targets:
                if self.dry:
                    print(t("wipe.dry_playlist", name=p["name"], n=p["num_tracks"]))
                    continue
                obj = getattr(self, "_pl_objects", {}).get(p["id"])
                if obj is None or not isinstance(obj, tidalapi.playlist.UserPlaylist):
                    # repli : recharger via l'API (factory renvoie UserPlaylist si on en est propriétaire)
                    try:
                        obj = self._call(self.session.playlist, p["id"])
                    except Exception as e:  # noqa: BLE001
                        print(t("wipe.playlist_not_found", name=p["name"], error=e))
                        continue
                if not isinstance(obj, tidalapi.playlist.UserPlaylist):
                    print(t("wipe.not_owned", name=p["name"]))
                    continue
                try:
                    self._call_ok(obj.delete)
                    print(t("wipe.deleted_playlist", name=p["name"]))
                except Exception as e:  # noqa: BLE001
                    print(t("wipe.playlist_error", name=p["name"], error=e))
            # (les playlists sont peu nombreuses : suppression en série, plus lisible)

        fav = self.session.user.favorites
        if favorites:
            ids = [t["id"] for t in snap["favorite_tracks"]]
            if self.dry:
                print(t("wipe.dry_favorites", n=len(ids)))
            else:
                self._parallel(fav.remove_track, ids, self.workers, t("label.favorites_removed"))
        if albums:
            ids = [a["id"] for a in snap["favorite_albums"]]
            if self.dry:
                print(t("wipe.dry_albums", n=len(ids)))
            else:
                self._parallel(fav.remove_album, ids, self.workers, t("label.albums_removed"))
        if artists:
            ids = [a["id"] for a in snap.get("favorite_artists", [])]
            if self.dry:
                print(t("wipe.dry_artists", n=len(ids)))
            else:
                self._parallel(fav.remove_artist, ids, self.workers, t("label.artists_removed"))
        if followed:
            ids = [p["id"] for p in snap.get("followed_playlists", [])]
            if self.dry:
                print(t("wipe.dry_followed", n=len(ids)))
            else:
                self._parallel(fav.remove_playlist, ids, self.workers,
                               t("label.playlists_unfollowed"))

    def verify_wipe(self, snap: dict, playlists: bool, favorites: bool, albums: bool,
                    only_names: set[str] | None = None) -> bool:
        """Relit le compte après suppression et signale ce qui reste."""
        if self.dry:
            return True
        ok = True
        fav = self.session.user.favorites
        if playlists:
            expected = {p["name"] for p in snap["playlists"]
                        if p["own"] and (only_names is None or p["name"] in only_names)}
            left = [p.name for p in self._call(self.session.user.playlists)
                    if p.name in expected]
            if left:
                ok = False
                print(t("verify.playlists_left", n=len(left), names=", ".join(left[:5]))
                      + (" …" if len(left) > 5 else ""))
        if favorites:
            n = len(self._paginate(fav.tracks))
            if n:
                ok = False
                print(t("verify.tracks_left", n=n))
        if albums:
            n = len(self._paginate(fav.albums))
            if n:
                ok = False
                print(t("verify.albums_left", n=n))
        print(t("verify.ok") if ok else t("verify.failed"))
        return ok

    # -- écritures
    def existing_playlists(self) -> dict[str, list[tidalapi.UserPlaylist]]:
        """Playlists du compte, groupees par nom. Une liste et non un objet :
        TIDAL autorise deux playlists homonymes, et un index par nom en perdait
        une — celle qu'on vidait ensuite n'etait pas forcement la bonne."""
        out: dict[str, list] = defaultdict(list)
        for p in self._call(self.session.user.playlists):
            out[p.name].append(p)
        return dict(out)

    def create_playlist(self, name: str, desc: str, track_ids: list[int],
                        existing: dict, overwrite: bool):
        if self.dry:
            print(t("playlist.dry", name=name, n=len(track_ids)))
            return
        same_name = existing.get(name) or []
        if len(same_name) > 1:
            # on ne peut pas deviner laquelle ecraser : ne rien toucher est la
            # seule reponse sure.
            print(t("playlist.ambiguous", n=len(same_name), name=name))
            return
        if same_name and not overwrite:
            print(t("playlist.exists", name=name))
            return
        if same_name and overwrite:
            pl = same_name[0]
            self._call(pl.clear)
        else:
            pl = self._call(self.session.user.create_playlist, name, desc)
        added = 0
        for i in range(0, len(track_ids), 100):
            chunk = [str(x) for x in track_ids[i:i + 100]]
            self._call(pl.add, chunk, allow_duplicates=True)
            added += len(chunk)
        print(t("playlist.created", name=name, n=added))

    def favorite_tracks(self, ids: list[int], skip_existing: bool = True):
        if self.dry:
            print(t("favorites.dry_tracks", n=len(ids)))
            return
        fav = self.session.user.favorites
        if skip_existing:
            try:
                have = {t.id for t in self._paginate(fav.tracks)}
                before = len(ids)
                ids = [x for x in ids if x not in have]
                if before - len(ids):
                    print(t("favorites.already", n=before - len(ids)))
            except Exception:  # noqa: BLE001
                pass
        if not ids:
            print(t("favorites.nothing"))
            return
        done = 0
        for i in range(0, len(ids), 50):
            chunk = [str(x) for x in ids[i:i + 50]]
            try:
                self._call(fav.add_track, chunk)
                done += len(chunk)
            except Exception:  # fallback un par un, en parallèle
                done += self._parallel(fav.add_track, chunk, self.workers,
                                       t("label.favorites_one_by_one"))
            print(t("favorites.progress", done=done, total=len(ids)), end="\r")
        print(t("favorites.added", n=done))

    def favorite_albums(self, ids: list[int], skip_existing: bool = True):
        if self.dry:
            print(t("favorites.dry_albums", n=len(ids)))
            return
        fav = self.session.user.favorites
        if skip_existing:
            try:
                have = {a.id for a in self._paginate(fav.albums)}
                ids = [x for x in ids if x not in have]
            except Exception:  # noqa: BLE001
                pass
        self._parallel(fav.add_album, ids, self.workers, t("label.albums_added"))
