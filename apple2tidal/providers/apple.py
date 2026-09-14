"""Source Apple Music : l'export XML de l'app Musique, ou le JSON d'export_apple_music.js."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path

from ..model import Album, Library, Playlist, Track

# Playlists système d'Apple Music à ignorer
SYSTEM_PLAYLIST_KEYS = {"Master", "Music", "Movies", "TV Shows", "Podcasts", "Audiobooks",
                        "Purchased", "Distinguished Kind", "Folder"}


class AppleExport:
    name = "Apple Music"

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> Library:
        return read_export(self.path)


def read_export(path: Path) -> Library:
    """Lit l'export une seule fois, albums compris."""
    if path.suffix.lower() == ".json":
        return parse_json_export(json.loads(path.read_text(encoding="utf-8")))
    return parse_xml(path)


def parse_library(path: Path) -> tuple[dict[str, Track], list[Playlist]]:
    lib = read_export(path)
    return lib.tracks, lib.playlists


def parse_albums(path: Path) -> list[Album]:
    """Albums declares par l'export JSON, avec leur UPC. L'export XML de l'app
    Musique ne liste pas les albums : liste vide, et `--albums` retombe sur
    `guess_albums`."""
    if path.suffix.lower() != ".json":
        return []
    return read_export(path).albums


def parse_json(path: Path) -> tuple[dict[str, Track], list[Playlist]]:
    """Export de export_apple_music.js (music.apple.com)."""
    lib = parse_json_export(json.loads(path.read_text(encoding="utf-8")))
    return lib.tracks, lib.playlists


def parse_json_export(data: dict) -> Library:
    def mk(s: dict) -> Track:
        return Track(
            name=s.get("name", ""), artist=s.get("artist", ""),
            album=s.get("album", ""),
            # album_artist vient de l'album parent ; les exports d'avant ce champ
            # retombent sur l'artiste du titre, comme avant.
            album_artist=s.get("album_artist") or s.get("artist", ""),
            duration_ms=int(s.get("duration_ms") or 0), loved=bool(s.get("loved")),
            year=int(s["year"]) if s.get("year") else None, isrc=s.get("isrc") or None,
        )

    tracks: dict[str, Track] = {str(s["id"]): mk(s) for s in data.get("songs", []) if s.get("name")}
    playlists: list[Playlist] = []
    for p in data.get("playlists", []):
        ids = []
        for s in p.get("tracks", []):
            if not s.get("name"):
                continue
            tracks.setdefault(str(s["id"]), mk(s))  # titre de playlist absent de la bibliothèque
            ids.append(str(s["id"]))
        if ids:
            playlists.append(Playlist(name=p["name"], track_ids=ids, smart=False))
    albums = [
        Album(name=al["name"], artist=al.get("artist") or "",
              track_count=int(al.get("track_count") or 0),
              upc=str(al["upc"]) if al.get("upc") else None)
        for al in data.get("albums", [])
        if al.get("name")
    ]
    return Library(tracks, playlists, albums)


def parse_xml(path: Path) -> Library:
    with open(path, "rb") as f:
        lib = plistlib.load(f)

    tracks: dict[str, Track] = {}
    for tid, raw in lib.get("Tracks", {}).items():
        kind = raw.get("Kind", "")
        if ("vid" in kind.lower() or raw.get("Has Video") or raw.get("Podcast")
                or raw.get("Movie") or raw.get("TV Show")):
            continue
        if not raw.get("Name"):
            continue
        tracks[str(tid)] = Track(
            name=raw.get("Name", ""),
            artist=raw.get("Artist", ""),
            album=raw.get("Album", ""),
            album_artist=raw.get("Album Artist", raw.get("Artist", "")),
            duration_ms=int(raw.get("Total Time", 0)),
            loved=bool(raw.get("Loved", False)),
            year=raw.get("Year"),
        )

    playlists: list[Playlist] = []
    for p in lib.get("Playlists", []):
        if any(p.get(k) for k in SYSTEM_PLAYLIST_KEYS):
            continue
        if p.get("Name") in ("Bibliothèque", "Library", "Musique", "Music"):
            continue
        ids = [str(it["Track ID"]) for it in p.get("Playlist Items", []) if str(it.get("Track ID")) in tracks]
        if not ids:
            continue
        playlists.append(Playlist(name=p["Name"], track_ids=ids, smart="Smart Info" in p))
    return Library(tracks, playlists)
