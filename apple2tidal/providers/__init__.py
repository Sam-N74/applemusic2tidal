"""Le contrat entre le moteur et les services.

Deux protocoles, parce que tous les services ne font pas les deux : l'export
Apple se lit et c'est tout, TIDAL se lit et s'ecrit. Un service qui fait les
deux implemente les deux dans la meme classe. Le moteur ne voit rien d'autre.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..model import Candidate, Library


class Source(Protocol):
    """Une bibliotheque a transferer."""

    def read(self) -> Library: ...


class Destination(Protocol):
    """Un service ou l'on ecrit. Les identifiants de titres et d'albums sont
    opaques pour le moteur : il les recoit du catalogue et les rend a l'ecriture.
    En mode `dry`, les methodes d'ecriture n'ecrivent rien et le disent : le
    moteur ne les filtre pas, c'est le service qui sait ce qu'il aurait fait."""

    dry: bool
    workers: int

    # -- catalogue
    def tracks_by_isrc(self, isrc: str) -> list[Candidate]: ...
    def search_tracks(self, query: str) -> list[Candidate]: ...
    def album_by_upc(self, upc: str) -> int | str | None: ...

    # -- ecriture
    def existing_playlists(self) -> dict[str, list[Any]]: ...
    def create_playlist(self, name: str, desc: str, track_ids: list, existing: dict,
                        overwrite: bool) -> None: ...
    def favorite_tracks(self, ids: list) -> None: ...
    def favorite_albums(self, ids: list) -> None: ...

    # -- suppressions : toujours sauvegarde, puis verification
    def snapshot(self, workers: int) -> dict: ...
    def wipe(self, snap: dict, playlists: bool, favorites: bool, albums: bool,
             only_names: set[str] | None = None, artists: bool = False,
             followed: bool = False) -> None: ...
    def verify_wipe(self, snap: dict, playlists: bool, favorites: bool, albums: bool,
                    only_names: set[str] | None = None) -> bool: ...
