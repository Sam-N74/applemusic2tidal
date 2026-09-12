"""Albums : lecture de la liste declaree par l'export, puis resolution cote TIDAL.

L'export JSON liste les albums de la bibliotheque, avec leur UPC. Les deviner a
partir des titres est un repli, pas le chemin principal.
"""

import json

import apple2tidal as a2t


def write_json(tmp_path, data) -> "a2t.Path":
    p = tmp_path / "apple_library.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ------------------------------------------------------------------ lecture
def test_parse_albums_reads_the_declared_list(tmp_path):
    path = write_json(tmp_path, {"songs": [], "albums": [
        {"id": "l.1", "name": "Kind of Blue", "artist": "Miles Davis",
         "track_count": 5, "upc": "886972394725"},
    ]})
    albums = a2t.parse_albums(path)
    assert [(a.name, a.artist, a.track_count, a.upc) for a in albums] == [
        ("Kind of Blue", "Miles Davis", 5, "886972394725"),
    ]


def test_parse_albums_tolerates_missing_fields(tmp_path):
    path = write_json(tmp_path, {"albums": [{"id": "l.1", "name": "Sans rien"}]})
    album = a2t.parse_albums(path)[0]
    assert (album.artist, album.track_count, album.upc) == ("", 0, None)


def test_parse_albums_skips_nameless_entries(tmp_path):
    path = write_json(tmp_path, {"albums": [{"id": "l.1", "name": ""},
                                            {"id": "l.2", "name": "Vrai"}]})
    assert [a.name for a in a2t.parse_albums(path)] == ["Vrai"]


def test_parse_albums_on_an_export_without_albums(tmp_path):
    assert a2t.parse_albums(write_json(tmp_path, {"songs": []})) == []


def test_parse_albums_on_xml_returns_nothing(tmp_path):
    """Le XML de l'app Musique ne liste pas les albums : rien a lire, pas d'erreur."""
    p = tmp_path / "Library.xml"
    p.write_bytes(b"")
    assert a2t.parse_albums(p) == []


# --------------------------------------------------------------- resolution
def cached(track, tidal_id=1, album_id=10):
    return {a2t.dedup_key(track): {"tidal_id": tidal_id, "album_id": album_id}}


def test_resolve_albums_uses_the_upc_first(mk_track):
    album = a2t.AppleAlbum(name="Kind of Blue", artist="Miles Davis", upc="886972394725")
    asked = []

    def lookup(upc):
        asked.append(upc)
        return 777

    found = a2t.resolve_albums([album], {}, {}, lookup)
    assert asked == ["886972394725"]
    assert [(a.name, tid, src) for a, tid, src in found] == [("Kind of Blue", 777, "upc")]


def test_resolve_albums_finds_an_album_owned_only_in_part(mk_track):
    """Un seul titre possede sur douze : l'UPC le retrouve, l'heuristique non."""
    album = a2t.AppleAlbum(name="Long Album", artist="Artiste", track_count=12, upc="1")
    track = mk_track(name="Titre 1", artist="Artiste", album="Long Album")
    found = a2t.resolve_albums([album], {"1": track}, cached(track), lambda upc: 42)
    assert [tid for _, tid, _ in found] == [42]


def test_resolve_albums_falls_back_to_the_matched_tracks(mk_track):
    album = a2t.AppleAlbum(name="Album", artist="Artiste", upc=None)
    tracks = {str(i): mk_track(tid=str(i), name=f"Titre {i}", artist="Artiste",
                               album="Album") for i in (1, 2, 3)}
    cache = {}
    for tr in tracks.values():
        cache.update(cached(tr, album_id=99))
    found = a2t.resolve_albums([album], tracks, cache, lambda upc: None)
    assert [(tid, src) for _, tid, src in found] == [(99, "tracks")]


def test_resolve_albums_groups_a_compilation_on_its_album_artist(mk_track):
    """Deux artistes differents, un seul album_artist : un seul album, pas deux."""
    album = a2t.AppleAlbum(name="Bande originale", artist="Various Artists")
    tracks = {
        "1": mk_track(tid="1", name="A", artist="Artiste A",
                      album="Bande originale", album_artist="Various Artists"),
        "2": mk_track(tid="2", name="B", artist="Artiste B",
                      album="Bande originale", album_artist="Various Artists"),
    }
    cache = {}
    for tr in tracks.values():
        cache.update(cached(tr, album_id=55))
    found = a2t.resolve_albums([album], tracks, cache, lambda upc: None)
    assert [tid for _, tid, _ in found] == [55]


def test_resolve_albums_skips_a_tie_between_two_tidal_albums(mk_track):
    """Un titre sur l'edition deluxe, un sur l'originale : rien de majoritaire."""
    album = a2t.AppleAlbum(name="Album", artist="Artiste")
    tracks = {"1": mk_track(tid="1", name="A", artist="Artiste", album="Album"),
              "2": mk_track(tid="2", name="B", artist="Artiste", album="Album")}
    cache = {**cached(tracks["1"], album_id=1), **cached(tracks["2"], album_id=2)}
    assert a2t.resolve_albums([album], tracks, cache, lambda upc: None) == []


def test_resolve_albums_never_returns_the_same_tidal_album_twice(mk_track):
    albums = [a2t.AppleAlbum(name="Album", artist="Artiste", upc="1"),
              a2t.AppleAlbum(name="Album (Deluxe)", artist="Artiste", upc="2")]
    found = a2t.resolve_albums(albums, {}, {}, lambda upc: 7)
    assert [tid for _, tid, _ in found] == [7]


def test_resolve_albums_ignores_an_album_with_no_matched_track(mk_track):
    album = a2t.AppleAlbum(name="Jamais trouve", artist="Artiste")
    assert a2t.resolve_albums([album], {}, {}, lambda upc: None) == []


# ------------------------------------------------- repli : deviner les albums
def test_guess_albums_keeps_the_old_heuristic(mk_track):
    """Sans liste declaree (export XML), on garde 3 titres minimum et 80 % de matchs."""
    tracks = {str(i): mk_track(tid=str(i), name=f"Titre {i}", artist="Artiste",
                               album="Album") for i in range(1, 6)}
    cache = {}
    for tr in tracks.values():
        cache.update(cached(tr, album_id=99))
    assert [tid for _, tid, _ in a2t.guess_albums(tracks, cache)] == [99]


def test_guess_albums_ignores_a_group_of_two(mk_track):
    tracks = {str(i): mk_track(tid=str(i), name=f"Titre {i}", artist="Artiste",
                               album="Album") for i in (1, 2)}
    cache = {}
    for tr in tracks.values():
        cache.update(cached(tr, album_id=99))
    assert a2t.guess_albums(tracks, cache) == []


def test_guess_albums_ignores_a_group_matched_below_eighty_percent(mk_track):
    tracks = {str(i): mk_track(tid=str(i), name=f"Titre {i}", artist="Artiste",
                               album="Album") for i in range(1, 6)}
    cache = cached(tracks["1"], album_id=99)   # 1 titre sur 5
    assert a2t.guess_albums(tracks, cache) == []
