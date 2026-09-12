"""Lecture des deux formats d'export : JSON (music.apple.com) et XML (app Musique)."""

import json
import plistlib

import apple2tidal as a2t


# ------------------------------------------------------------------- JSON
def write_json(tmp_path, data) -> "a2t.Path":
    p = tmp_path / "apple_library.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_parse_json_reads_tracks_and_playlists(tmp_path):
    path = write_json(tmp_path, {
        "songs": [
            {"id": "1", "name": "Song A", "artist": "Artist A", "album": "Album A",
             "duration_ms": 200000, "isrc": "USUM71703861", "year": "2017", "loved": True},
            {"id": "2", "name": "Song B", "artist": "Artist B", "album": "Album B",
             "duration_ms": 180000, "isrc": None, "year": None},
        ],
        "playlists": [
            {"id": "p1", "name": "Road trip", "tracks": [
                {"id": "1", "name": "Song A", "artist": "Artist A", "album": "Album A"},
            ]},
        ],
    })
    tracks, playlists = a2t.parse_library(path)

    assert set(tracks) == {"1", "2"}
    assert tracks["1"].isrc == "USUM71703861"
    assert tracks["1"].loved is True
    assert tracks["1"].year == 2017
    assert tracks["2"].isrc is None
    assert tracks["2"].year is None
    assert [p.name for p in playlists] == ["Road trip"]
    assert playlists[0].track_ids == ["1"]


def test_parse_json_adds_playlist_only_tracks(tmp_path):
    """Un titre present dans une playlist mais absent de la bibliotheque doit exister quand meme."""
    path = write_json(tmp_path, {
        "songs": [],
        "playlists": [{"id": "p1", "name": "Mix", "tracks": [
            {"id": "99", "name": "Orpheline", "artist": "X", "album": "Y", "duration_ms": 1000},
        ]}],
    })
    tracks, playlists = a2t.parse_library(path)
    assert tracks["99"].name == "Orpheline"
    assert playlists[0].track_ids == ["99"]


def test_parse_json_skips_nameless_and_empty_playlists(tmp_path):
    path = write_json(tmp_path, {
        "songs": [{"id": "1", "name": "", "artist": "A"}],
        "playlists": [{"id": "p1", "name": "Vide", "tracks": []},
                      {"id": "p2", "name": "Sans titres", "tracks": [{"id": "3", "name": ""}]}],
    })
    tracks, playlists = a2t.parse_library(path)
    assert tracks == {}
    assert playlists == []


def test_parse_json_tolerates_missing_fields(tmp_path):
    """L'export peut renvoyer des champs absents ou nuls : pas d'exception."""
    path = write_json(tmp_path, {"songs": [{"id": "1", "name": "Song"}]})
    tracks, playlists = a2t.parse_library(path)
    assert tracks["1"].duration_ms == 0
    assert tracks["1"].artist == ""
    assert tracks["1"].loved is False
    assert playlists == []


def test_parse_json_reads_the_album_artist(tmp_path):
    """Sur une compilation, l'artiste de l'album n'est pas celui du titre."""
    path = write_json(tmp_path, {"songs": [
        {"id": "1", "name": "Titre", "artist": "Artiste A", "album": "Bande originale",
         "album_artist": "Various Artists"},
    ]})
    tracks, _ = a2t.parse_library(path)
    assert tracks["1"].album_artist == "Various Artists"


def test_parse_json_falls_back_to_the_track_artist(tmp_path):
    """Export plus ancien, sans album_artist : le comportement precedent reste."""
    path = write_json(tmp_path, {"songs": [
        {"id": "1", "name": "Titre", "artist": "Artiste A", "album": "Album"},
    ]})
    tracks, _ = a2t.parse_library(path)
    assert tracks["1"].album_artist == "Artiste A"


def test_parse_json_groups_a_compilation_into_one_album(tmp_path):
    """Le regroupement d'albums suit album_artist : sinon chaque titre fait album."""
    path = write_json(tmp_path, {"songs": [
        {"id": "1", "name": "A", "artist": "Artiste A", "album": "Bande originale",
         "album_artist": "Various Artists"},
        {"id": "2", "name": "B", "artist": "Artiste B", "album": "Bande originale",
         "album_artist": "Various Artists"},
    ]})
    tracks, _ = a2t.parse_library(path)
    assert len(a2t.tracks_by_album(tracks)) == 1


# -------------------------------------------------------------------- XML
def write_xml(tmp_path, tracks: dict, playlists: list) -> "a2t.Path":
    p = tmp_path / "Library.xml"
    with open(p, "wb") as f:
        plistlib.dump({"Tracks": tracks, "Playlists": playlists}, f)
    return p


def test_parse_xml_reads_tracks(tmp_path):
    path = write_xml(tmp_path, {
        "101": {"Name": "Song A", "Artist": "Artist A", "Album": "Album A",
                "Album Artist": "Album Artist A", "Total Time": 200000,
                "Loved": True, "Year": 1999, "Kind": "MPEG audio file"},
    }, [
        {"Name": "Road trip", "Playlist Items": [{"Track ID": 101}]},
    ])
    tracks, playlists = a2t.parse_library(path)

    assert tracks["101"].album_artist == "Album Artist A"
    assert tracks["101"].loved is True
    assert tracks["101"].year == 1999
    assert tracks["101"].isrc is None      # le XML iTunes ne porte pas d'ISRC
    assert playlists[0].track_ids == ["101"]


def test_parse_xml_skips_video_and_podcast(tmp_path):
    path = write_xml(tmp_path, {
        "1": {"Name": "Clip", "Kind": "MPEG-4 video file", "Total Time": 1},
        "2": {"Name": "Episode", "Podcast": True, "Total Time": 1},
        "3": {"Name": "Film", "Movie": True, "Total Time": 1},
        "4": {"Name": "Serie", "TV Show": True, "Total Time": 1},
        "5": {"Name": "Vrai titre", "Artist": "A", "Total Time": 1},
    }, [])
    tracks, _ = a2t.parse_library(path)
    assert list(tracks) == ["5"]


def test_parse_xml_skips_system_playlists(tmp_path):
    path = write_xml(tmp_path, {
        "1": {"Name": "Song", "Artist": "A", "Total Time": 1},
    }, [
        {"Name": "Bibliotheque entiere", "Master": True, "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Podcasts", "Podcasts": True, "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Library", "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Ma playlist", "Playlist Items": [{"Track ID": 1}]},
    ])
    _, playlists = a2t.parse_library(path)
    assert [p.name for p in playlists] == ["Ma playlist"]


def test_parse_xml_flags_smart_playlists(tmp_path):
    path = write_xml(tmp_path, {"1": {"Name": "S", "Artist": "A", "Total Time": 1}}, [
        {"Name": "Normale", "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Intelligente", "Smart Info": b"\x00", "Playlist Items": [{"Track ID": 1}]},
    ])
    _, playlists = a2t.parse_library(path)
    assert {p.name: p.smart for p in playlists} == {"Normale": False, "Intelligente": True}


def test_parse_xml_ignores_unknown_track_ids(tmp_path):
    path = write_xml(tmp_path, {"1": {"Name": "S", "Artist": "A", "Total Time": 1}}, [
        {"Name": "Ma playlist", "Playlist Items": [{"Track ID": 1}, {"Track ID": 404}]},
    ])
    _, playlists = a2t.parse_library(path)
    assert playlists[0].track_ids == ["1"]
