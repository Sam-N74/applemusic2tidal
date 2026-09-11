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
class Match:
    tidal_id: int | None
    score: float
    tidal_title: str = ""
    tidal_artist: str = ""
    album_id: int | None = None


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
            album=s.get("album", ""), album_artist=s.get("artist", ""),
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


def parse_xml(path: Path) -> tuple[dict[str, AppleTrack], list[ApplePlaylist]]:
    with open(path, "rb") as f:
        lib = plistlib.load(f)

    tracks: dict[str, AppleTrack] = {}
    for tid, t in lib.get("Tracks", {}).items():
        kind = t.get("Kind", "")
        if "vid" in kind.lower() or t.get("Has Video") or t.get("Podcast") or t.get("Movie") or t.get("TV Show"):
            continue
        if not t.get("Name"):
            continue
        tracks[str(tid)] = AppleTrack(
            id=str(tid),
            name=t.get("Name", ""),
            artist=t.get("Artist", ""),
            album=t.get("Album", ""),
            album_artist=t.get("Album Artist", t.get("Artist", "")),
            duration_ms=int(t.get("Total Time", 0)),
            loved=bool(t.get("Loved", False)),
            year=t.get("Year"),
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


def score_candidate(a: AppleTrack, t: tidalapi.Track) -> float:
    title_s = fuzz.token_set_ratio(norm(clean_title(a.name)), norm(clean_title(t.name)))
    t_artists = " ".join(x.name for x in (t.artists or [t.artist]) if x)
    artist_s = max(
        fuzz.token_set_ratio(norm(a.artist), norm(t_artists)),
        fuzz.token_set_ratio(norm(clean_artist(a.artist)), norm(clean_artist(t.artist.name if t.artist else ""))),
    )
    album_s = fuzz.token_set_ratio(norm(clean_title(a.album)), norm(clean_title(t.album.name if t.album else "")))

    score = 0.55 * title_s + 0.35 * artist_s + 0.10 * album_s
    # Pénalité durée : > 5 s d'écart pénalise, > 20 s disqualifie quasi
    if a.duration_ms and t.duration:
        diff = abs(a.duration_ms / 1000 - t.duration)
        if diff > 20:
            score -= 25
        elif diff > 5:
            score -= 8
    # Bonus si titre exact
    if norm(a.name) == norm(t.name):
        score += 3
    return score


def dedup_key(a: AppleTrack) -> str:
    """Clé d'identité d'un titre : deux entrées Apple identiques = une seule recherche TIDAL."""
    if a.isrc:
        return "isrc:" + a.isrc.upper()
    return "q:" + norm(clean_artist(a.artist)) + "|" + norm(clean_title(a.name))


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
            sys.exit("Connexion TIDAL échouée.")
        print(f"[TIDAL] connecté : {self.session.user.username if hasattr(self.session.user, 'username') else self.session.user.id}")

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
                            print(f"  [rate-limit] pause {wait}s")
                    continue
                if i == retries - 1:
                    raise
                time.sleep(1)
        return None

    def search_tracks(self, query: str, limit: int = 12) -> list[tidalapi.Track]:
        try:
            res = self._call(self.session.search, query, models=[tidalapi.Track], limit=limit)
        except Exception as e:  # noqa: BLE001
            print(f"  [search err] {query!r}: {e}")
            return []
        return list(res.get("tracks", [])) if isinstance(res, dict) else list(getattr(res, "tracks", []))

    def match(self, a: AppleTrack, threshold: float) -> Match:
        # 1) ISRC : exact
        if a.isrc:
            try:
                cands = [t for t in self._call(self.session.get_tracks_by_isrc, a.isrc) if t.available]
            except Exception:  # noqa: BLE001
                cands = []
            if cands:
                t = max(cands, key=lambda x: (score_candidate(a, x), x.popularity or 0))
                return Match(t.id, 100.0, t.name, t.artist.name if t.artist else "",
                             t.album.id if t.album else None)
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
            for t in self.search_tracks(q):
                if t.id in seen or not t.available:
                    continue
                seen.add(t.id)
                s = score_candidate(a, t)
                if best is None or s > best[0]:
                    best = (s, t)
            if best and best[0] >= 92:  # assez bon, inutile de continuer
                break
        if best and best[0] >= threshold:
            s, t = best
            return Match(t.id, round(s, 1), t.name, t.artist.name if t.artist else "",
                         t.album.id if t.album else None)
        return Match(None, round(best[0], 1) if best else 0.0)

    def _call_ok(self, fn, *a, **kw) -> bool:
        """Comme _call, mais traite un retour False comme un échec (tidalapi
        renvoie booléen sur delete/remove au lieu de lever une exception)."""
        r = self._call(fn, *a, **kw)
        if r is False:
            raise RuntimeError("l'API TIDAL a refusé l'opération (retour False)")
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
                    print(f"  [err] {label} {futs[f]}: {e}")
                print(f"  {label} : {done}/{len(items)}", end="\r")
        print(f"  [ok] {label} : {done}/{len(items)}      ")
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
                    print(f"  [dry] supprimerait la playlist « {p['name']} » ({p['num_tracks']} titres)")
                    continue
                obj = getattr(self, "_pl_objects", {}).get(p["id"])
                if obj is None or not isinstance(obj, tidalapi.playlist.UserPlaylist):
                    # repli : recharger via l'API (factory renvoie UserPlaylist si on en est propriétaire)
                    try:
                        obj = self._call(self.session.playlist, p["id"])
                    except Exception as e:  # noqa: BLE001
                        print(f"  [err] « {p['name']} » introuvable : {e}")
                        continue
                if not isinstance(obj, tidalapi.playlist.UserPlaylist):
                    print(f"  [skip] « {p['name']} » n'est pas une playlist que tu possèdes")
                    continue
                try:
                    self._call_ok(obj.delete)
                    print(f"  [del] playlist « {p['name']} »")
                except Exception as e:  # noqa: BLE001
                    print(f"  [err] playlist « {p['name']} » : {e}")
            # (les playlists sont peu nombreuses : suppression en série, plus lisible)

        fav = self.session.user.favorites
        if favorites:
            ids = [t["id"] for t in snap["favorite_tracks"]]
            if self.dry:
                print(f"  [dry] retirerait {len(ids)} titres des favoris")
            else:
                self._parallel(fav.remove_track, ids, self.workers, "favoris retirés")
        if albums:
            ids = [a["id"] for a in snap["favorite_albums"]]
            if self.dry:
                print(f"  [dry] retirerait {len(ids)} albums des favoris")
            else:
                self._parallel(fav.remove_album, ids, self.workers, "albums retirés")
        if artists:
            ids = [a["id"] for a in snap.get("favorite_artists", [])]
            if self.dry:
                print(f"  [dry] retirerait {len(ids)} artistes des favoris")
            else:
                self._parallel(fav.remove_artist, ids, self.workers, "artistes retirés")
        if followed:
            ids = [p["id"] for p in snap.get("followed_playlists", [])]
            if self.dry:
                print(f"  [dry] arrêterait de suivre {len(ids)} playlist(s)")
            else:
                self._parallel(fav.remove_playlist, ids, self.workers, "playlists non suivies")

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
                print(f"  [!] {len(left)} playlist(s) toujours présentes : {', '.join(left[:5])}"
                      + (" …" if len(left) > 5 else ""))
        if favorites:
            n = len(self._paginate(fav.tracks))
            if n:
                ok = False
                print(f"  [!] {n} titres encore en favoris")
        if albums:
            n = len(self._paginate(fav.albums))
            if n:
                ok = False
                print(f"  [!] {n} albums encore en favoris")
        print("  [vérif] compte bien vidé" if ok else
              "  [vérif] la suppression a échoué (voir ci-dessus) — rien ne sera importé par-dessus")
        return ok

    # -- écritures
    def existing_playlists(self) -> dict[str, tidalapi.UserPlaylist]:
        return {p.name: p for p in self._call(self.session.user.playlists)}

    def create_playlist(self, name: str, desc: str, track_ids: list[int],
                        existing: dict, overwrite: bool):
        if self.dry:
            print(f"  [dry] playlist « {name} » : {len(track_ids)} titres")
            return
        pl = existing.get(name)
        if pl and not overwrite:
            print(f"  [skip] « {name} » existe déjà (--overwrite pour la vider et recréer)")
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
        print(f"  [ok] « {name} » : {added} titres")

    def favorite_tracks(self, ids: list[int], skip_existing: bool = True):
        if self.dry:
            print(f"  [dry] {len(ids)} titres → favoris")
            return
        fav = self.session.user.favorites
        if skip_existing:
            try:
                have = {t.id for t in self._paginate(fav.tracks)}
                before = len(ids)
                ids = [x for x in ids if x not in have]
                if before - len(ids):
                    print(f"  {before - len(ids)} déjà en favoris, ignorés")
            except Exception:  # noqa: BLE001
                pass
        if not ids:
            print("  [ok] rien à ajouter")
            return
        done = 0
        for i in range(0, len(ids), 50):
            chunk = [str(x) for x in ids[i:i + 50]]
            try:
                self._call(fav.add_track, chunk)
                done += len(chunk)
            except Exception:  # fallback un par un, en parallèle
                done += self._parallel(fav.add_track, chunk, self.workers, "favoris (unitaire)")
            print(f"  favoris : {done}/{len(ids)}", end="\r")
        print(f"  [ok] {done} titres ajoutés aux favoris")

    def favorite_albums(self, ids: list[int], skip_existing: bool = True):
        if self.dry:
            print(f"  [dry] {len(ids)} albums → favoris")
            return
        fav = self.session.user.favorites
        if skip_existing:
            try:
                have = {a.id for a in self._paginate(fav.albums)}
                ids = [x for x in ids if x not in have]
            except Exception:  # noqa: BLE001
                pass
        self._parallel(fav.add_album, ids, self.workers, "albums ajoutés")


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
        print(f"[cache] {CACHE_FILE} illisible ({e}), on repart de zéro")
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
    print(f"[cache] {n} entrées migrées vers le nouveau format ({len(out)} uniques)")
    return out


def save_cache(cache: dict):
    STATE_DIR.mkdir(exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(CACHE_FILE)  # atomique : jamais de fichier à moitié écrit


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def main():
    # Windows : console/fichiers en UTF-8, et on coupe le bruit "Track 'x' is unavailable" de tidalapi
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.getLogger("tidalapi").setLevel(logging.ERROR)

    ap = argparse.ArgumentParser(description="Apple Music → TIDAL")
    ap.add_argument("library", type=Path, nargs="?",
                    help="Export .xml (app Musique) ou .json (export_apple_music.js). "
                         "Inutile avec --wipe.")
    ap.add_argument("--playlists", action="store_true", help="Recréer les playlists")
    ap.add_argument("--favorites", action="store_true", help="Toute la bibliothèque → titres favoris TIDAL")
    ap.add_argument("--loved", action="store_true", help="Seulement les titres 'aimés' → favoris")
    ap.add_argument("--albums", action="store_true", help="Albums complets (≥80%% des titres) → albums favoris")
    ap.add_argument("--all", action="store_true", help="= --playlists --favorites --albums")
    ap.add_argument("--only", action="append", default=[], help="Nom(s) de playlist à traiter uniquement")
    ap.add_argument("--skip-smart", action="store_true", help="Ignorer les playlists intelligentes")
    ap.add_argument("--overwrite", action="store_true", help="Vider et recréer les playlists existantes")
    ap.add_argument("--wipe", action="store_true",
                    help="DESTRUCTIF : vide entièrement le compte TIDAL et s'arrête (aucun import)")
    ap.add_argument("--keep-followed", action="store_true",
                    help="Avec --wipe : garder les playlists d'autres utilisateurs que tu suis")
    ap.add_argument("--reset", action="store_true",
                    help="DESTRUCTIF : vide le compte TIDAL (playlists créées par toi + favoris) avant l'import")
    ap.add_argument("--reset-scope", default="imported", choices=["imported", "all"],
                    help="imported (défaut) = ne supprime que les playlists portant le nom d'une playlist Apple ; "
                         "all = supprime toutes tes playlists")
    ap.add_argument("--yes", action="store_true", help="Ne pas demander confirmation pour --reset")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"Score min de matching (0-100), défaut {DEFAULT_THRESHOLD:.0f}")
    ap.add_argument("--rematch", action="store_true", help="Ignorer le cache et rechercher à nouveau les non-trouvés")
    ap.add_argument("--dry-run", action="store_true", help="Ne rien écrire sur TIDAL")
    ap.add_argument("--delay", type=float, default=0.0, help="Pause entre requêtes (s), 0 par défaut")
    ap.add_argument("--workers", type=int, default=8,
                    help="Requêtes TIDAL en parallèle (défaut 8 ; baisser si rate-limit)")
    args = ap.parse_args()

    if args.wipe and (args.all or args.playlists or args.favorites or args.loved
                      or args.albums or args.reset):
        ap.error("--wipe supprime et s'arrête : ne le combine pas avec une action d'import "
                 "(utilise --reset pour vider puis réimporter)")
    if not args.wipe and args.library is None:
        ap.error("chemin de l'export manquant")
    if args.all:
        args.playlists = args.favorites = args.albums = True
    if args.reset and not (args.playlists or args.favorites or args.loved or args.albums):
        ap.error("--reset s'utilise avec l'action d'import correspondante (ex : --reset --all)")
    if not (args.wipe or args.playlists or args.favorites or args.loved
            or args.albums or args.dry_run):
        ap.error("Précise au moins une action : --playlists / --favorites / --loved / "
                 "--albums / --all / --wipe")

    # ---- Mode --wipe : vider le compte, puis s'arrêter
    if args.wipe:
        tidal = Tidal(dry_run=args.dry_run, delay=args.delay, workers=args.workers)
        if args.dry_run:
            print("\n[TIDAL] --dry-run actif : SIMULATION, rien ne sera supprimé.")
        print("[TIDAL] lecture de l'état du compte…")
        snap = tidal.snapshot(workers=args.workers)
        own = [p for p in snap["playlists"] if p["own"]]
        foll = snap.get("followed_playlists", [])
        print(f"  sauvegarde écrite dans {snap['path']}")
        print(f"  à supprimer : {len(own)} playlist(s), "
              f"{len(snap['favorite_tracks'])} titres favoris, "
              f"{len(snap['favorite_albums'])} albums favoris, "
              f"{len(snap.get('favorite_artists', []))} artistes favoris"
              + ("" if args.keep_followed else f", {len(foll)} playlist(s) suivies"))
        for p in own:
            print(f"    - {p['name']} ({p['num_tracks']})")
        if not args.dry_run and not args.yes:
            print("\n  Le compte sera vidé et RIEN ne sera réimporté. C'est IRRÉVERSIBLE.")
            if input("  Tape SUPPRIMER pour confirmer : ").strip() != "SUPPRIMER":
                sys.exit("  Annulé, rien n'a été touché.")
        tidal.wipe(snap, playlists=True, favorites=True, albums=True, only_names=None,
                   artists=True, followed=not args.keep_followed)
        ok = tidal.verify_wipe(snap, playlists=True, favorites=True, albums=True)
        print("\nTerminé." if ok else "\nTerminé avec des erreurs (voir ci-dessus).")
        return

    tracks, playlists = parse_library(args.library)
    n_isrc = sum(1 for t in tracks.values() if t.isrc)
    if args.skip_smart:
        playlists = [p for p in playlists if not p.smart]
    if args.only:
        wanted = {n.lower() for n in args.only}
        playlists = [p for p in playlists if p.name.lower() in wanted]
    print(f"[Apple] {len(tracks)} titres ({n_isrc} avec ISRC), {len(playlists)} playlists")
    for p in playlists:
        print(f"   - {p.name} ({len(p.track_ids)}){' [smart]' if p.smart else ''}")

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
            print("\n[TIDAL] --dry-run actif : SIMULATION, rien ne sera supprimé.")
        print("\n[TIDAL] état actuel du compte…")
        snap = tidal.snapshot(workers=args.workers)
        own = [p for p in snap["playlists"] if p["own"]]
        only = {p.name for p in playlists} if args.reset_scope == "imported" else None
        to_del = [p for p in own if only is None or p["name"] in only]
        n_fav = len(snap["favorite_tracks"]) if (args.favorites or args.loved) else 0
        n_alb = len(snap["favorite_albums"]) if args.albums else 0
        print(f"  sauvegarde écrite dans {snap['path']}")
        print(f"  à supprimer : {len(to_del)} playlist(s) sur {len(own)}, "
              f"{n_fav} titres favoris, {n_alb} albums favoris")
        for p in to_del:
            print(f"    - {p['name']} ({p['num_tracks']})")
        if not args.dry_run and not args.yes:
            print("\n  C'est IRRÉVERSIBLE. TIDAL ne propose pas de corbeille.")
            if input("  Tape SUPPRIMER pour confirmer : ").strip() != "SUPPRIMER":
                sys.exit("  Annulé, rien n'a été touché.")
        tidal.wipe(snap, playlists=args.playlists, favorites=(args.favorites or args.loved),
                   albums=args.albums, only_names=only)
        if not tidal.verify_wipe(snap, playlists=args.playlists,
                                 favorites=(args.favorites or args.loved),
                                 albums=args.albums, only_names=only):
            sys.exit("Import annulé : le compte n'a pas été vidé comme demandé.")

    cache = migrate_cache(load_cache(), tracks)

    # Un seul lookup par titre distinct : les doublons entre playlists sont gratuits
    groups: dict[str, AppleTrack] = {}
    for tid in needed:
        groups.setdefault(dedup_key(tracks[tid]), tracks[tid])
    todo = [k for k in groups
            if k not in cache or (args.rematch and cache[k].get("tidal_id") is None)]
    print(f"[match] {len(needed)} titres → {len(groups)} distincts, "
          f"{len(todo)} à rechercher (cache : {len(groups) - len(todo)})")

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
            print(f"  {i}/{len(todo)} {flag} {a.artist} — {a.name}  ({m.score})"
                  + (f"  → {m.tidal_artist} — {m.tidal_title}" if m.tidal_id else ""))
            if i % 50 == 0:
                save_cache(cache)

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for f in as_completed([ex.submit(work, k) for k in todo]):
                try:
                    f.result()
                except Exception as e:  # noqa: BLE001
                    print(f"  [err] {e}")
    save_cache(cache)
    if todo:
        rate = len(todo) / max(time.time() - t0, 0.001)
        print(f"[match] terminé en {time.time() - t0:.0f}s ({rate:.1f} titres/s)")

    def tidal_id(apple_id: str) -> int | None:
        return cache.get(dedup_key(tracks[apple_id]), {}).get("tidal_id")

    # Rapport des non-trouvés
    unmatched = [tracks[t] for t in needed if not tidal_id(t)]
    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["artist", "title", "album", "best_score", "playlists"])
        pl_of = defaultdict(list)
        for p in playlists:
            for t in p.track_ids:
                pl_of[t].append(p.name)
        for a in sorted(unmatched, key=lambda x: (x.artist.lower(), x.name.lower())):
            w.writerow([a.artist, a.name, a.album,
                        cache.get(dedup_key(a), {}).get("score", 0),
                        "; ".join(pl_of.get(a.id, []))])
    print(f"[match] {len(needed) - len(unmatched)}/{len(needed)} trouvés — non trouvés listés dans {REPORT_FILE}")

    # ---- Playlists
    if args.playlists:
        print("\n[TIDAL] playlists")
        existing = {} if args.dry_run else tidal.existing_playlists()
        for p in playlists:
            ids, seen = [], set()
            for t in p.track_ids:
                x = tidal_id(t)
                if x and x not in seen:
                    ids.append(x)
                    seen.add(x)
            if not ids:
                print(f"  [skip] « {p.name} » : aucun titre trouvé")
                continue
            miss = len(p.track_ids) - len(ids)
            desc = "Importée d'Apple Music" + (f" ({miss} titres non trouvés)" if miss else "")
            tidal.create_playlist(p.name, desc, ids, existing, args.overwrite)

    # ---- Favoris
    if args.favorites or args.loved:
        print("\n[TIDAL] favoris")
        src = tracks.values() if args.favorites else (t for t in tracks.values() if t.loved)
        ids = list(dict.fromkeys(x for x in (tidal_id(t.id) for t in src) if x))
        tidal.favorite_tracks(ids)

    # ---- Albums
    if args.albums:
        print("\n[TIDAL] albums")
        by_album: dict[tuple[str, str], list[AppleTrack]] = defaultdict(list)
        for t in tracks.values():
            if t.album:
                by_album[(norm(t.album_artist), norm(t.album))].append(t)
        album_ids: list[int] = []
        for (_, _), ts in by_album.items():
            if len(ts) < 3:
                continue  # singles / EP partiels : on laisse
            tidal_albums = [cache.get(dedup_key(t), {}).get("album_id") for t in ts if tidal_id(t.id)]
            tidal_albums = [x for x in tidal_albums if x]
            if len(tidal_albums) / len(ts) >= 0.8:
                # album TIDAL majoritaire parmi les matchs
                top = max(set(tidal_albums), key=tidal_albums.count)
                if top not in album_ids:
                    album_ids.append(top)
        print(f"  {len(album_ids)} albums complets détectés")
        tidal.favorite_albums(album_ids)

    print("\nTerminé.")


if __name__ == "__main__":
    main()
