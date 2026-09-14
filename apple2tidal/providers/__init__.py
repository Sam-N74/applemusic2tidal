"""Le contrat entre le moteur et les services.

Deux protocoles, parce que tous les services ne font pas les deux : l'export
Apple se lit et c'est tout, TIDAL se lit et s'ecrit. Un service qui fait les
deux implemente les deux dans la meme classe. Le moteur ne voit rien d'autre.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, TypedDict

from ..model import Candidate, Library


class Source(Protocol):
    """Une bibliotheque a transferer. `name` est le nom affiche."""

    name: str

    def read(self) -> Library: ...


class SnapshotPlaylist(TypedDict, total=False):
    """Une playlist telle que la sauvegarde la decrit."""
    id: int | str
    name: str
    num_tracks: int
    own: bool          # creee par le compte connecte, donc supprimable


class SnapshotItem(TypedDict, total=False):
    """Un titre, un album ou un artiste de la sauvegarde. `id` suffit a la
    suppression ; le nom et l'artiste ne servent qu'a relire le fichier."""
    id: int | str
    name: str
    artist: str


class Snapshot(TypedDict, total=False):
    """Ce que `snapshot()` renvoie, et que `wipe()` et `verify_wipe()` relisent.

    La forme est la meme pour tous les services, parce que c'est elle que la
    ligne de commande affiche et compte avant de demander confirmation : elle
    ne doit pas avoir a savoir a quel service elle parle. `path` est le fichier
    de sauvegarde ecrit au passage — l'utilisateur doit pouvoir le nommer avant
    d'accepter une suppression.

    Un service qui ne distingue pas les playlists suivies des siennes met tout
    dans `playlists` avec `own` a jour ; `followed_playlists` reste alors vide.
    """
    path: Path | None
    saved_at: str
    playlists: list[SnapshotPlaylist]
    favorite_tracks: list[SnapshotItem]
    favorite_albums: list[SnapshotItem]
    favorite_artists: list[SnapshotItem]
    followed_playlists: list[SnapshotItem]


class Destination(Protocol):
    """Un service ou l'on ecrit. Les identifiants de titres et d'albums sont
    opaques pour le moteur : il les recoit du catalogue et les rend a l'ecriture.
    En mode `dry`, les methodes d'ecriture n'ecrivent rien et le disent : le
    moteur ne les filtre pas, c'est le service qui sait ce qu'il aurait fait.

    `name` est le nom affiche. Il ne sert qu'a ca, mais il fait partie du
    contrat : les sections du transfert annoncent ou elles ecrivent, et cette
    information cesse d'etre decorative des qu'il y a deux destinations."""

    name: str
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
    def snapshot(self, workers: int) -> Snapshot: ...
    def wipe(self, snap: Snapshot, playlists: bool, favorites: bool, albums: bool,
             only_names: set[str] | None = None, artists: bool = False,
             followed: bool = False) -> None: ...
    def verify_wipe(self, snap: Snapshot, playlists: bool, favorites: bool, albums: bool,
                    only_names: set[str] | None = None) -> bool: ...
