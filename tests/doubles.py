"""Doubles partages par les tests : titres, candidats, client TIDAL sans OAuth,
et une destination en memoire qui respecte le contrat de `providers`."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

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
