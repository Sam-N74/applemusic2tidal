#!/usr/bin/env python3
"""
apple2tidal — transfère bibliothèque, playlists et favoris d'Apple Music vers TIDAL.

Source   : export XML de l'app Musique (Fichier > Bibliothèque > Exporter la bibliothèque…)
           OU export JSON depuis music.apple.com via export_apple_music.js (contient les ISRC → matching exact)
Cible    : TIDAL via la lib non officielle `tidalapi` (login OAuth navigateur)

Usage rapide :
    python apple2tidal.py Bibliothèque.xml --dry-run          # analyse + matching, rien n'est écrit
    python apple2tidal.py Bibliothèque.xml --playlists         # crée les playlists
    python apple2tidal.py Bibliothèque.xml --favorites         # toute la bibliothèque en favoris
    python apple2tidal.py Bibliothèque.xml --loved --albums    # titres aimés + albums complets
    python apple2tidal.py Bibliothèque.xml --all               # tout

Le matching est mis en cache dans .apple2tidal/matches.json : relancer = reprendre.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import plistlib
import re
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from messages import LANGUAGES, resolve_lang, set_lang, t

try:
    import tidalapi
except ImportError:
    sys.exit("pip install tidalapi rapidfuzz")

try:
    from rapidfuzz import fuzz
except ImportError:  # fallback stdlib
    import difflib

    class fuzz:  # type: ignore
        @staticmethod
        def token_set_ratio(a: str, b: str) -> float:
            sa, sb = set(a.split()), set(b.split())
            inter = " ".join(sorted(sa & sb))
            return 100 * max(
                difflib.SequenceMatcher(None, inter, " ".join(sorted(sa))).ratio(),
                difflib.SequenceMatcher(None, inter, " ".join(sorted(sb))).ratio(),
                difflib.SequenceMatcher(None, a, b).ratio(),
            )

        @staticmethod
        def ratio(a: str, b: str) -> float:
            return 100 * difflib.SequenceMatcher(None, a, b).ratio()


STATE_DIR = Path(".apple2tidal")
SESSION_FILE = STATE_DIR / "tidal_session.json"
CACHE_FILE = STATE_DIR / "matches.json"
REPORT_FILE = STATE_DIR / "unmatched.csv"

# Score minimal pour accepter un match fuzzy. En dessous, le titre part dans unmatched.csv.
DEFAULT_THRESHOLD = 78.0

# Playlists système d'Apple Music à ignorer
SYSTEM_PLAYLIST_KEYS = {"Master", "Music", "Movies", "TV Shows", "Podcasts", "Audiobooks",
                        "Purchased", "Distinguished Kind", "Folder"}


# --------------------------------------------------------------------------- #
#  Modèles
# --------------------------------------------------------------------------- #
@dataclass
class AppleTrack:
    id: str
    name: str
    artist: str
    album: str
    album_artist: str
    duration_ms: int
    loved: bool
    year: int | None = None
    isrc: str | None = None

    @property
    def key(self) -> str:
        return str(self.id)


@dataclass
class ApplePlaylist:
    name: str
    track_ids: list[str] = field(default_factory=list)
    smart: bool = False


@dataclass
class AppleAlbum:
    """Album declare par l'export. L'UPC identifie la sortie, comme l'ISRC un titre."""
    name: str
    artist: str
    track_count: int = 0
    upc: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (norm(self.artist), norm(self.name))


@dataclass
class Match:
    tidal_id: int | None
    score: float
    tidal_title: str = ""
    tidal_artist: str = ""
    album_id: int | None = None
    threshold: float = 0.0   # seuil sous lequel la decision a ete prise


# --------------------------------------------------------------------------- #
#  Parsing de l'export Apple Music
# --------------------------------------------------------------------------- #
def parse_library(path: Path) -> tuple[dict[str, AppleTrack], list[ApplePlaylist]]:
    if path.suffix.lower() == ".json":
        return parse_json(path)
    return parse_xml(path)


def parse_json(path: Path) -> tuple[dict[str, AppleTrack], list[ApplePlaylist]]:
    """Export de export_apple_music.js (music.apple.com)."""
    data = json.loads(path.read_text(encoding="utf-8"))

    def mk(s: dict) -> AppleTrack:
        return AppleTrack(
            id=str(s["id"]), name=s.get("name", ""), artist=s.get("artist", ""),
            album=s.get("album", ""),
            # album_artist vient de l'album parent ; les exports d'avant ce champ
            # retombent sur l'artiste du titre, comme avant.
            album_artist=s.get("album_artist") or s.get("artist", ""),
            duration_ms=int(s.get("duration_ms") or 0), loved=bool(s.get("loved")),
            year=int(s["year"]) if s.get("year") else None, isrc=s.get("isrc") or None,
        )

    tracks: dict[str, AppleTrack] = {str(s["id"]): mk(s) for s in data.get("songs", []) if s.get("name")}
    playlists: list[ApplePlaylist] = []
    for p in data.get("playlists", []):
        ids = []
        for s in p.get("tracks", []):
            if not s.get("name"):
                continue
            tracks.setdefault(str(s["id"]), mk(s))  # titre de playlist absent de la bibliothèque
            ids.append(str(s["id"]))
        if ids:
            playlists.append(ApplePlaylist(name=p["name"], track_ids=ids, smart=False))
    return tracks, playlists


def parse_albums(path: Path) -> list[AppleAlbum]:
    """Albums declares par l'export JSON, avec leur UPC.

    L'export XML de l'app Musique ne liste pas les albums : on renvoie une liste
    vide, et `--albums` retombe sur `guess_albums`.
    """
    if path.suffix.lower() != ".json":
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        AppleAlbum(name=al["name"], artist=al.get("artist") or "",
                   track_count=int(al.get("track_count") or 0),
                   upc=str(al["upc"]) if al.get("upc") else None)
        for al in data.get("albums", [])
        if al.get("name")
    ]


def parse_xml(path: Path) -> tuple[dict[str, AppleTrack], list[ApplePlaylist]]:
    with open(path, "rb") as f:
        lib = plistlib.load(f)

    tracks: dict[str, AppleTrack] = {}
    for tid, raw in lib.get("Tracks", {}).items():
        kind = raw.get("Kind", "")
        if ("vid" in kind.lower() or raw.get("Has Video") or raw.get("Podcast")
                or raw.get("Movie") or raw.get("TV Show")):
            continue
        if not raw.get("Name"):
            continue
        tracks[str(tid)] = AppleTrack(
            id=str(tid),
            name=raw.get("Name", ""),
            artist=raw.get("Artist", ""),
            album=raw.get("Album", ""),
            album_artist=raw.get("Album Artist", raw.get("Artist", "")),
            duration_ms=int(raw.get("Total Time", 0)),
            loved=bool(raw.get("Loved", False)),
            year=raw.get("Year"),
        )

    playlists: list[ApplePlaylist] = []
    for p in lib.get("Playlists", []):
        if any(p.get(k) for k in SYSTEM_PLAYLIST_KEYS):
            continue
        if p.get("Name") in ("Bibliothèque", "Library", "Musique", "Music"):
            continue
        ids = [str(it["Track ID"]) for it in p.get("Playlist Items", []) if str(it.get("Track ID")) in tracks]
        if not ids:
            continue
        playlists.append(ApplePlaylist(name=p["Name"], track_ids=ids, smart="Smart Info" in p))
    return tracks, playlists


# --------------------------------------------------------------------------- #
#  Normalisation & scoring
# --------------------------------------------------------------------------- #
_PAREN_JUNK = re.compile(
    r"\s*[\(\[\-–—]\s*(feat\.?|ft\.?|featuring|remaster(ed)?|\d{4} remaster|mono|stereo|"
    r"live|edit|radio edit|single version|album version|deluxe|bonus track|explicit|"
    r"clean|original mix|extended|version|remix)[^\)\]]*[\)\]]?\s*$",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", "and").replace("’", "'")
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title(s: str) -> str:
    prev = None
    while prev != s:
        prev, s = s, _PAREN_JUNK.sub("", s)
    return s.strip()


def clean_artist(s: str) -> str:
    s = _FEAT.sub("", s)
    return re.split(r"\s*[,&/]\s*|\s+x\s+", s)[0].strip() or s


def score_candidate(a: AppleTrack, cand: tidalapi.Track) -> float:
    title_s = fuzz.token_set_ratio(norm(clean_title(a.name)), norm(clean_title(cand.name)))
    cand_artists = " ".join(x.name for x in (cand.artists or [cand.artist]) if x)
    artist_s = max(
        fuzz.token_set_ratio(norm(a.artist), norm(cand_artists)),
        fuzz.token_set_ratio(norm(clean_artist(a.artist)),
                             norm(clean_artist(cand.artist.name if cand.artist else ""))),
    )
    album_s = fuzz.token_set_ratio(norm(clean_title(a.album)),
                                   norm(clean_title(cand.album.name if cand.album else "")))

    score = 0.55 * title_s + 0.35 * artist_s + 0.10 * album_s
    # Pénalité durée : > 5 s d'écart pénalise, > 20 s disqualifie quasi
    if a.duration_ms and cand.duration:
        diff = abs(a.duration_ms / 1000 - cand.duration)
        if diff > 20:
            score -= 25
        elif diff > 5:
            score -= 8
    # Bonus si titre exact
    if norm(a.name) == norm(cand.name):
        score += 3
    return score


def dedup_key(a: AppleTrack) -> str:
    """Clé d'identité d'un titre : deux entrées Apple identiques = une seule recherche TIDAL."""
    if a.isrc:
        return "isrc:" + a.isrc.upper()
    return "q:" + norm(clean_artist(a.artist)) + "|" + norm(clean_title(a.name))


# --------------------------------------------------------------------------- #
#  Albums
# --------------------------------------------------------------------------- #
def tracks_by_album(tracks: dict[str, AppleTrack]) -> dict[tuple[str, str], list[AppleTrack]]:
    """Regroupe les titres par (artiste de l'album, album). L'artiste de l'album,
    pas celui du titre : sinon une compilation eclate en autant d'albums."""
    out: dict[tuple[str, str], list[AppleTrack]] = defaultdict(list)
    for a in tracks.values():
        if a.album:
            out[(norm(a.album_artist), norm(a.album))].append(a)
    return out


def _majority_album_id(ts: list[AppleTrack], cache: dict) -> int | None:
    """Album TIDAL majoritaire parmi les titres deja matches, ou None si egalite."""
    ids = [x for x in (cache.get(dedup_key(a), {}).get("album_id") for a in ts) if x]
    if not ids:
        return None
    top = max(set(ids), key=ids.count)
    return top if ids.count(top) * 2 > len(ids) else None


def resolve_albums(declared: list[AppleAlbum], tracks: dict[str, AppleTrack],
                   cache: dict, upc_lookup) -> list[tuple[AppleAlbum, int, str]]:
    """Associe chaque album declare par l'export a un album TIDAL.

    Deux chemins : l'UPC quand l'export le fournit — exact, et il retrouve aussi
    les albums dont on ne possede qu'une partie des titres — sinon l'album TIDAL
    majoritaire parmi les titres matches. `upc_lookup` est injecte pour que cette
    fonction reste testable sans compte TIDAL.

    Renvoie [(album, id TIDAL, "upc" | "tracks")].
    """
    grouped = tracks_by_album(tracks)
    out: list[tuple[AppleAlbum, int, str]] = []
    seen: set[int] = set()
    for al in declared:
        tidal_id, source = (upc_lookup(al.upc) if al.upc else None), "upc"
        if tidal_id is None:
            tidal_id, source = _majority_album_id(grouped.get(al.key, []), cache), "tracks"
        if tidal_id and tidal_id not in seen:
            seen.add(tidal_id)
            out.append((al, tidal_id, source))
    return out


def guess_albums(tracks: dict[str, AppleTrack],
                 cache: dict) -> list[tuple[AppleAlbum, int, str]]:
    """Repli pour l'export XML, qui ne declare aucun album : on les devine a
    partir des titres, avec le meme garde-fou qu'avant — au moins 3 titres dans
    la bibliotheque et 80 % d'entre eux matches."""
    out: list[tuple[AppleAlbum, int, str]] = []
    seen: set[int] = set()
    for ts in tracks_by_album(tracks).values():
        if len(ts) < 3:
            continue
        matched = [a for a in ts if cache.get(dedup_key(a), {}).get("album_id")]
        if len(matched) / len(ts) < 0.8:
            continue
        tidal_id = _majority_album_id(matched, cache)
        if tidal_id and tidal_id not in seen:
            seen.add(tidal_id)
            out.append((AppleAlbum(name=ts[0].album, artist=ts[0].album_artist,
                                   track_count=len(ts)), tidal_id, "tracks"))
    return out


# --------------------------------------------------------------------------- #
#  Client TIDAL
# --------------------------------------------------------------------------- #
class Tidal:
    def __init__(self, dry_run: bool, delay: float = 0.0, workers: int = 8):
        self.dry = dry_run
        self.delay = delay
        self.workers = workers
        self._lock = threading.Lock()
        self._pause_until = 0.0  # backoff global partagé entre les threads
        self.session = tidalapi.Session()
        STATE_DIR.mkdir(exist_ok=True)
        ok = self.session.login_session_file(SESSION_FILE)
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

    def search_tracks(self, query: str, limit: int = 12) -> list[tidalapi.Track]:
        try:
            res = self._call(self.session.search, query, models=[tidalapi.Track], limit=limit)
        except Exception as e:  # noqa: BLE001
            print(t("search.error", query=query, error=e))
            return []
        return list(res.get("tracks", [])) if isinstance(res, dict) else list(getattr(res, "tracks", []))

    def match(self, a: AppleTrack, threshold: float) -> Match:
        # 1) ISRC : exact
        if a.isrc:
            try:
                cands = [c for c in self._call(self.session.get_tracks_by_isrc, a.isrc)
                         if c.available]
            except Exception:  # noqa: BLE001
                cands = []
            if cands:
                cand = max(cands, key=lambda x: (score_candidate(a, x), x.popularity or 0))
                return Match(cand.id, 100.0, cand.name, cand.artist.name if cand.artist else "",
                             cand.album.id if cand.album else None, threshold)
        # 2) recherche fuzzy
        queries = [
            f"{clean_artist(a.artist)} {clean_title(a.name)}",
            f"{a.artist} {a.name}",
            f"{clean_title(a.name)} {clean_artist(a.album_artist)}",
            clean_title(a.name),
        ]
        seen: set[int] = set()
        best: tuple[float, tidalapi.Track] | None = None
        for q in dict.fromkeys(q.strip() for q in queries if q.strip()):
            for cand in self.search_tracks(q):
                if cand.id in seen or not cand.available:
                    continue
                seen.add(cand.id)
                s = score_candidate(a, cand)
                if best is None or s > best[0]:
                    best = (s, cand)
            if best and best[0] >= 92:  # assez bon, inutile de continuer
                break
        if best and best[0] >= threshold:
            s, cand = best
            return Match(cand.id, round(s, 1), cand.name, cand.artist.name if cand.artist else "",
                         cand.album.id if cand.album else None, threshold)
        return Match(None, round(best[0], 1) if best else 0.0, threshold=threshold)

    def album_by_upc(self, upc: str) -> int | None:
        """Album TIDAL portant cet UPC. tidalapi leve quand il ne trouve rien."""
        try:
            albums = self._call(self.session.get_albums_by_barcode, upc)
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
        STATE_DIR.mkdir(exist_ok=True)
        path = STATE_DIR / f"backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
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
    def existing_playlists(self) -> dict[str, tidalapi.UserPlaylist]:
        return {p.name: p for p in self._call(self.session.user.playlists)}

    def create_playlist(self, name: str, desc: str, track_ids: list[int],
                        existing: dict, overwrite: bool):
        if self.dry:
            print(t("playlist.dry", name=name, n=len(track_ids)))
            return
        pl = existing.get(name)
        if pl and not overwrite:
            print(t("playlist.exists", name=name))
            return
        if pl and overwrite:
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


# --------------------------------------------------------------------------- #
#  Cache
# --------------------------------------------------------------------------- #
def load_cache() -> dict[str, dict]:
    if not CACHE_FILE.exists():
        return {}
    try:
        txt = CACHE_FILE.read_text(encoding="utf-8").strip()
        return json.loads(txt) if txt else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(t("cache.unreadable", path=CACHE_FILE, error=e))
        return {}


def migrate_cache(cache: dict, tracks: dict) -> dict:
    """Ancien cache indexé par ID Apple -> réindexé par clé d'identité."""
    if not cache or any(k.startswith(("isrc:", "q:")) for k in cache):
        return cache
    out, n = {}, 0
    for apple_id, m in cache.items():
        a = tracks.get(apple_id)
        if a:
            out.setdefault(dedup_key(a), m)
            n += 1
    print(t("cache.migrated", n=n, unique=len(out)))
    return out


def needs_match(entry: dict | None, threshold: float, rematch: bool) -> bool:
    """Faut-il (re)chercher ce titre sur TIDAL ?

    Une entree de cache porte une decision — accepte ou non — prise sous un seuil
    donne. Si le seuil courant renversait cette decision, l'entree est perimee :
    remonter --threshold doit vraiment durcir le tri, le baisser doit vraiment
    rouvrir les titres refuses de peu. Un cache anterieur a ce champ se juge sur
    son score, qui suffit a trancher.
    """
    if not entry:
        return True
    matched = entry.get("tidal_id") is not None
    if rematch and not matched:
        return True
    return matched != (entry.get("score", 0.0) >= threshold)


def save_cache(cache: dict):
    STATE_DIR.mkdir(exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(CACHE_FILE)  # atomique : jamais de fichier à moitié écrit


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
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

    ap = argparse.ArgumentParser(description=t("cli.description"))
    ap.add_argument("library", type=Path, nargs="?", help=t("cli.help.library"))
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
    if not args.wipe and args.library is None:
        ap.error(t("cli.error.library_missing"))
    if args.all:
        args.playlists = args.favorites = args.albums = True
    if args.reset and not (args.playlists or args.favorites or args.loved or args.albums):
        ap.error(t("cli.error.reset_needs_action"))
    if not (args.wipe or args.playlists or args.favorites or args.loved
            or args.albums or args.dry_run):
        ap.error(t("cli.error.no_action"))

    # ---- Mode --wipe : vider le compte, puis s'arrêter
    if args.wipe:
        tidal = Tidal(dry_run=args.dry_run, delay=args.delay, workers=args.workers)
        if args.dry_run:
            print("\n" + t("main.dry_run_notice"))
        print(t("main.reading_account"))
        snap = tidal.snapshot(workers=args.workers)
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
        tidal.wipe(snap, playlists=True, favorites=True, albums=True, only_names=None,
                   artists=True, followed=not args.keep_followed)
        ok = tidal.verify_wipe(snap, playlists=True, favorites=True, albums=True)
        print("\n" + (t("main.done") if ok else t("main.done_with_errors")))
        return

    tracks, playlists = parse_library(args.library)
    n_isrc = sum(1 for t in tracks.values() if t.isrc)
    if args.skip_smart:
        playlists = [p for p in playlists if not p.smart]
    if args.only:
        wanted = {n.lower() for n in args.only}
        playlists = [p for p in playlists if p.name.lower() in wanted]
    print(t("apple.summary", tracks=len(tracks), isrc=n_isrc, playlists=len(playlists)))
    for p in playlists:
        print(t("apple.playlist_line", name=p.name, n=len(p.track_ids),
                smart=t("apple.smart_tag") if p.smart else ""))

    # Quels titres faut-il matcher ?
    needed: set[str] = set()
    if args.playlists:
        for p in playlists:
            needed.update(p.track_ids)
    if args.favorites or args.albums:
        needed.update(tracks)
    if args.loved:
        needed.update(t.id for t in tracks.values() if t.loved)
    if args.dry_run and not needed:
        needed.update(tracks)

    tidal = Tidal(dry_run=args.dry_run, delay=args.delay, workers=args.workers)

    # ---- Reset du compte TIDAL (avant tout import)
    if args.reset:
        if args.dry_run:
            print("\n" + t("main.dry_run_notice"))
        print("\n" + t("main.current_account"))
        snap = tidal.snapshot(workers=args.workers)
        own = [p for p in snap["playlists"] if p["own"]]
        only = {p.name for p in playlists} if args.reset_scope == "imported" else None
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
        tidal.wipe(snap, playlists=args.playlists, favorites=(args.favorites or args.loved),
                   albums=args.albums, only_names=only)
        if not tidal.verify_wipe(snap, playlists=args.playlists,
                                 favorites=(args.favorites or args.loved),
                                 albums=args.albums, only_names=only):
            sys.exit(t("main.import_cancelled"))

    cache = migrate_cache(load_cache(), tracks)

    # Un seul lookup par titre distinct : les doublons entre playlists sont gratuits
    groups: dict[str, AppleTrack] = {}
    for tid in needed:
        groups.setdefault(dedup_key(tracks[tid]), tracks[tid])
    todo = [k for k in groups if needs_match(cache.get(k), args.threshold, args.rematch)]
    stale = sum(1 for k in todo if k in cache)
    if stale:
        print(t("cache.threshold_changed", n=stale, threshold=args.threshold))
    print(t("match.plan", needed=len(needed), groups=len(groups), todo=len(todo),
            cached=len(groups) - len(todo)))

    t0 = time.time()
    lock = threading.Lock()
    counter = [0]

    def work(key: str):
        a = groups[key]
        m = tidal.match(a, args.threshold)
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
                save_cache(cache)

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for f in as_completed([ex.submit(work, k) for k in todo]):
                try:
                    f.result()
                except Exception as e:  # noqa: BLE001
                    print(t("match.error", error=e))
    save_cache(cache)
    if todo:
        rate = len(todo) / max(time.time() - t0, 0.001)
        print(t("match.finished", seconds=time.time() - t0, rate=rate))

    def tidal_id(apple_id: str) -> int | None:
        return cache.get(dedup_key(tracks[apple_id]), {}).get("tidal_id")

    # Rapport des non-trouvés
    unmatched = [tracks[tid] for tid in needed if not tidal_id(tid)]
    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["artist", "title", "album", "best_score", "playlists"])
        pl_of = defaultdict(list)
        for p in playlists:
            for tid in p.track_ids:
                pl_of[tid].append(p.name)
        for a in sorted(unmatched, key=lambda x: (x.artist.lower(), x.name.lower())):
            w.writerow([a.artist, a.name, a.album,
                        cache.get(dedup_key(a), {}).get("score", 0),
                        "; ".join(pl_of.get(a.id, []))])
    print(t("match.report", found=len(needed) - len(unmatched), total=len(needed),
            path=REPORT_FILE))

    # ---- Playlists
    if args.playlists:
        print("\n" + t("section.playlists"))
        existing = {} if args.dry_run else tidal.existing_playlists()
        for p in playlists:
            ids, seen = [], set()
            for tid in p.track_ids:
                x = tidal_id(tid)
                if x and x not in seen:
                    ids.append(x)
                    seen.add(x)
            if not ids:
                print(t("playlist.no_match", name=p.name))
                continue
            miss = len(p.track_ids) - len(ids)
            desc = t("playlist.description") + (t("playlist.description_missing", n=miss)
                                                if miss else "")
            tidal.create_playlist(p.name, desc, ids, existing, args.overwrite)

    # ---- Favoris
    if args.favorites or args.loved:
        print("\n" + t("section.favorites"))
        src = tracks.values() if args.favorites else (a for a in tracks.values() if a.loved)
        ids = list(dict.fromkeys(x for x in (tidal_id(a.id) for a in src) if x))
        tidal.favorite_tracks(ids)

    # ---- Albums
    if args.albums:
        print("\n" + t("section.albums"))
        declared = parse_albums(args.library)
        found = (resolve_albums(declared, tracks, cache, tidal.album_by_upc)
                 if declared else guess_albums(tracks, cache))
        if not declared:
            print(t("albums.no_declared_list"))
        for al, _, source in found:
            print(t("albums.line", artist=al.artist, name=al.name,
                    source=t("albums.source_upc") if source == "upc"
                    else t("albums.source_tracks")))
        print(t("albums.detected", n=len(found)))
        tidal.favorite_albums([tid for _, tid, _ in found])

    print("\n" + t("main.done"))


if __name__ == "__main__":
    main()
