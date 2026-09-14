"""apple2tidal — transfère une bibliothèque Apple Music vers TIDAL.

Ce module expose la surface publique du paquet. Le moteur est dans `engine`
et `matching`, le modèle dans `model`, les services dans `providers/`.
"""

from .albums import cached_upc_lookup, guess_albums, resolve_albums, tracks_by_album
from .cli import confirmed, main
from .engine import Options, transfer
from .matching import DEFAULT_THRESHOLD, match, needs_match, score_candidate
from .model import (
    Album,
    Candidate,
    Library,
    Match,
    Playlist,
    Track,
    clean_artist,
    clean_title,
    dedup_key,
    norm,
)
from .providers.apple import AppleExport, parse_albums, parse_json, parse_library, parse_xml, read_export
from .providers.spotify import Spotify
from .providers.tidal import Tidal
from .state import Store, load_cache, migrate_cache, save_cache

__all__ = [
    "Album", "AppleExport", "Candidate", "DEFAULT_THRESHOLD", "Library", "Match", "Options",
    "Playlist", "Spotify", "Store", "Tidal", "Track", "cached_upc_lookup", "clean_artist", "clean_title",
    "confirmed", "dedup_key", "guess_albums", "load_cache", "main", "match", "migrate_cache",
    "needs_match", "norm", "parse_albums", "parse_json", "parse_library", "parse_xml",
    "read_export", "resolve_albums", "save_cache", "score_candidate", "tracks_by_album", "transfer",
]
