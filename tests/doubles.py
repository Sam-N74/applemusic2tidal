"""Doubles partages par les tests : titres, candidats, client TIDAL sans OAuth,
et une destination en memoire qui respecte le contrat de `providers`."""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import apple2tidal as a2t
from apple2tidal.model import Candidate, norm


def track(name="Song", artist="Artist", album="Album", album_artist=None,
          duration_ms=200_000, loved=False, isrc=None, tid="1"):
    """`tid` est accepte pour les appelants qui l'ecrivent aussi en cle de dict ;
    l'identifiant de source n'est plus un champ du titre."""
    return a2t.Track(
        name=name, artist=artist, album=album,
        album_artist=album_artist if album_artist is not None else artist,
        duration_ms=duration_ms, loved=loved, isrc=isrc,
    )


def tidal_track(name="Song", artist="Artist", album="Album", duration=200, popularity=50):
    """Un candidat tel que l'adaptateur TIDAL le livre au moteur."""
    return Candidate(
        id=abs(hash((name, artist))) % 10**6,
        name=name, artists=[artist], album=album, album_id=1,
        duration_s=duration, popularity=popularity,
    )


def make_client(store_dir: Path | None = None, dry_run=False, workers=2) -> a2t.Tidal:
    """Fabrique un client Tidal sans passer par __init__, qui exige un OAuth.
    Sans `store_dir`, l'etat pointe vers un dossier temporaire jamais ecrit :
    seul `snapshot()` s'en sert."""
    c = a2t.Tidal.__new__(a2t.Tidal)
    c.store = a2t.Store(store_dir or Path(tempfile.gettempdir()) / "apple2tidal-tests")
    c.dry = dry_run
    c.delay = 0.0
    c.workers = workers
    c._lock = threading.Lock()
    c._pause_until = 0.0
    c.session = MagicMock()
    c.session.user.favorites = MagicMock()
    return c


class MemoryDestination:
    """Un service fictif, entierement en memoire.

    C'est la preuve du contrat : si le moteur fait un transfert complet avec
    cette classe sans qu'on le retouche, ajouter un vrai service est un
    adaptateur et rien d'autre.
    """

    name = "memory"

    def __init__(self, catalog: list[Candidate], isrcs: dict[str, int | str] | None = None,
                 upcs: dict[str, int | str] | None = None, dry_run=False, workers=1):
        self.catalog = catalog
        self.isrcs = isrcs or {}          # ISRC -> id de candidat
        self.upcs = upcs or {}            # UPC -> id d'album
        self.dry = dry_run
        self.workers = workers
        self.playlists: dict[str, list] = {}
        self.favorites: list = []
        self.saved_albums: list = []
        self.searches: list[str] = []

    # -- catalogue
    def tracks_by_isrc(self, isrc):
        wanted = self.isrcs.get(isrc.upper())
        return [c for c in self.catalog if c.id == wanted]

    def search_tracks(self, query):
        self.searches.append(query)
        words = set(norm(query).split())
        return [c for c in self.catalog
                if words & set(norm(c.name + " " + " ".join(c.artists)).split())]

    def album_by_upc(self, upc):
        return self.upcs.get(upc)

    # -- ecriture
    def existing_playlists(self):
        return {name: [name] for name in self.playlists}

    def create_playlist(self, name, desc, track_ids, existing, overwrite):
        if self.dry or (name in existing and not overwrite):
            return
        self.playlists[name] = list(track_ids)

    def favorite_tracks(self, ids):
        if not self.dry:
            self.favorites.extend(x for x in ids if x not in self.favorites)

    def favorite_albums(self, ids):
        if not self.dry:
            self.saved_albums.extend(x for x in ids if x not in self.saved_albums)

    # -- suppressions
    def snapshot(self, workers):
        return {"playlists": [{"id": n, "name": n, "num_tracks": len(ids), "own": True}
                              for n, ids in self.playlists.items()],
                "favorite_tracks": [{"id": i} for i in self.favorites],
                "favorite_albums": [{"id": i} for i in self.saved_albums],
                "favorite_artists": [], "followed_playlists": []}

    def wipe(self, snap, playlists, favorites, albums, only_names=None,
             artists=False, followed=False):
        if playlists:
            for p in snap["playlists"]:
                if only_names is None or p["name"] in only_names:
                    self.playlists.pop(p["name"], None)
        if favorites:
            self.favorites.clear()
        if albums:
            self.saved_albums.clear()

    def verify_wipe(self, snap, playlists, favorites, albums, only_names=None):
        return ((not playlists or not any(only_names is None or n in only_names
                                          for n in self.playlists))
                and (not favorites or not self.favorites)
                and (not albums or not self.saved_albums))


# --------------------------------------------------------------------------- #
#  Spotify : un compte en memoire derriere l'interface de requests.Session
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = {} if payload is None else payload
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    @property
    def content(self):
        return self.text.encode("utf-8")

    def json(self):
        return self._payload


class StubAuth:
    """Un jeton toujours valide. Le flux PKCE a ses propres tests."""

    def __init__(self, token="jeton"):
        self._token = token
        self.forgotten = 0

    def token(self):
        return self._token

    def forget(self):
        self.forgotten += 1


def spotify_track(tid="t1", name="Song", artist="Artist", album="Album",
                  album_artist=None, duration_ms=200_000, isrc=None, popularity=50,
                  album_id="al1", album_upc=None, **extra):
    """Un titre tel que l'API Spotify le renvoie."""
    body = {
        "id": tid, "type": "track", "name": name, "duration_ms": duration_ms,
        "popularity": popularity,
        "artists": [{"id": "ar1", "name": artist}],
        "album": {"id": album_id, "name": album,
                  "artists": [{"id": "ar1", "name": album_artist or artist}],
                  "release_date": "2011-05-02",
                  "external_ids": {"upc": album_upc} if album_upc else {}},
        "external_ids": {"isrc": isrc} if isrc else {},
    }
    body.update(extra)
    return body


def spotify_album(aid="al1", name="Album", artist="Artist", upc=None, total_tracks=10):
    return {"id": aid, "name": name, "total_tracks": total_tracks,
            "artists": [{"id": "ar1", "name": artist}],
            "external_ids": {"upc": upc} if upc else {}}


def spotify_playlist(pid="p1", name="Road trip", owner="sam", collaborative=False, total=0):
    return {"id": pid, "name": name, "collaborative": collaborative,
            "owner": {"id": owner, "display_name": owner},
            "items": {"total": total}, "external_urls": {"spotify": "https://x/" + pid}}


class FakeSpotify:
    """L'API Spotify, en memoire. Seul le reseau est double : le code exerce est
    le vrai `SpotifyApi`, avec sa pagination, ses paquets et ses reessais."""

    def __init__(self, me="sam", saved_tracks=None, saved_albums=None, playlists=None,
                 playlist_items=None, artists=None, catalog=None):
        self.me = {"id": me, "display_name": me}
        self.saved_tracks = list(saved_tracks or [])
        self.saved_albums = list(saved_albums or [])
        self.playlists = list(playlists or [])
        self.playlist_items = {k: list(v) for k, v in (playlist_items or {}).items()}
        self.artists = list(artists or [])
        self.catalog = list(catalog or [])
        self.calls = []
        self.answers = []          # reponses forcees, consommees dans l'ordre

    # -- interface de requests.Session
    def request(self, method, url, params=None, json=None, timeout=None, headers=None):
        if self.answers:
            return self.answers.pop(0)
        parsed = urlparse(url)
        path = parsed.path.split("/v1", 1)[-1]
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        query.update({k: str(v) for k, v in (params or {}).items()})
        self.calls.append((method, path, query, json))
        try:
            return FakeResponse(200, self._route(method, path, query, json))
        except KeyError as unknown:
            return FakeResponse(404, {"error": {"message": "no route " + str(unknown)}})

    # -- routage
    def _route(self, method, path, query, body):
        if path == "/me" and method == "GET":
            return dict(self.me)
        if path == "/me/tracks" and method == "GET":
            return self._page([{"added_at": "2020-01-01T00:00:00Z", "track": t}
                               for t in self.saved_tracks], path, query)
        if path == "/me/albums" and method == "GET":
            return self._page([{"album": a} for a in self.saved_albums], path, query)
        if path == "/me/playlists" and method == "GET":
            return self._page(self.playlists, path, query)
        if path == "/me/following" and method == "GET":
            return {"artists": self._page(self.artists, path, query)}
        if path == "/me/playlists" and method == "POST":
            created = spotify_playlist(pid=f"new{len(self.playlists) + 1}",
                                       name=body["name"], owner=self.me["id"])
            self.playlists.append(created)
            self.playlist_items[created["id"]] = []
            return created
        if path == "/me/library":
            return self._library(method, query.get("uris", ""))
        if path == "/search":
            return self._search(query)
        if path.startswith("/playlists/") and path.endswith("/items"):
            return self._items(method, path.split("/")[2], body, path, query)
        raise KeyError(method + " " + path)

    def _page(self, items, path, query):
        limit = int(query.get("limit") or 20)
        offset = int(query.get("offset") or 0)
        following = offset + limit
        nxt = None
        if following < len(items):
            keep = "&type=" + query["type"] if query.get("type") else ""
            nxt = (f"https://api.spotify.com/v1{path}"
                   f"?offset={following}&limit={limit}{keep}")
        return {"items": items[offset:following], "total": len(items), "next": nxt}

    def _items(self, method, playlist_id, body, path, query):
        kept = self.playlist_items.setdefault(playlist_id, [])
        if method == "GET":
            return self._page([{"track": t} for t in kept], path, query)
        ids = [u.rsplit(":", 1)[-1] for u in (body or {}).get("uris", [])]
        found = [self._catalog_track(x) for x in ids]
        if method == "PUT":
            kept[:] = found
        else:
            kept.extend(found)
        return {"snapshot_id": f"s{len(kept)}"}

    def _library(self, method, uris):
        for uri in [u for u in uris.split(",") if u]:
            _, kind, ident = uri.split(":")
            if kind == "track":
                self._toggle(self.saved_tracks, self._catalog_track(ident), method)
            elif kind == "album":
                self._toggle(self.saved_albums, spotify_album(aid=ident), method)
            elif kind == "artist":
                self._toggle(self.artists, {"id": ident, "name": ident}, method)
            elif kind == "playlist":
                self._toggle(self.playlists, spotify_playlist(pid=ident), method)
        return {}

    @staticmethod
    def _toggle(collection, item, method):
        present = [x for x in collection if x["id"] == item["id"]]
        if method == "PUT" and not present:
            collection.append(item)
        if method == "DELETE":
            for x in present:
                collection.remove(x)

    def _catalog_track(self, tid):
        for entry in self.catalog:
            if entry["id"] == tid:
                return entry
        return spotify_track(tid=tid)

    def _search(self, query):
        q = query.get("q", "")
        limit = int(query.get("limit") or 10)
        if query.get("type") == "album":
            upc = q.split("upc:", 1)[-1] if q.startswith("upc:") else None
            hits = [a for a in self.catalog_albums()
                    if upc and (a.get("external_ids") or {}).get("upc") == upc]
            return {"albums": {"items": hits[:limit]}}
        if q.startswith("isrc:"):
            wanted = q.split(":", 1)[1]
            hits = [x for x in self.catalog
                    if (x.get("external_ids") or {}).get("isrc") == wanted]
        else:
            words = set(norm(q).split())
            hits = [x for x in self.catalog
                    if words & set(norm(x["name"] + " "
                                        + " ".join(a["name"] for a in x["artists"])).split())]
        return {"tracks": {"items": hits[:limit]}}

    def catalog_albums(self):
        """Les albums du catalogue, deduits des titres : un album connu est un
        album dont un titre existe."""
        seen, out = set(), []
        for entry in self.catalog:
            album = entry.get("album") or {}
            if album.get("id") and album["id"] not in seen:
                seen.add(album["id"])
                out.append(album)
        return out
