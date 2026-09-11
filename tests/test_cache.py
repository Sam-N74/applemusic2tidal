"""Cache disque des matchs : lecture tolerante, ecriture atomique, migration."""

import json

import apple2tidal as a2t


def test_load_cache_missing_file(state_dir):
    assert a2t.load_cache() == {}


def test_save_then_load_roundtrip(state_dir):
    cache = {"isrc:USUM71703861": {"tidal_id": 123, "score": 100.0}}
    a2t.save_cache(cache)
    assert a2t.load_cache() == cache


def test_save_cache_leaves_no_temp_file(state_dir):
    a2t.save_cache({"q:a|b": {"tidal_id": None, "score": 0.0}})
    assert not (state_dir / "matches.tmp").exists()
    assert (state_dir / "matches.json").exists()


def test_load_cache_survives_corrupted_file(state_dir, capsys):
    """Un cache tronque (interruption brutale) ne doit pas faire planter le run."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "matches.json").write_text('{"a": {"tidal', encoding="utf-8")
    assert a2t.load_cache() == {}
    assert "unreadable" in capsys.readouterr().out


def test_load_cache_empty_file(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "matches.json").write_text("   ", encoding="utf-8")
    assert a2t.load_cache() == {}


def test_save_cache_handles_non_ascii(state_dir):
    cache = {"q:bjork|joga": {"tidal_id": 1, "tidal_title": "Jóga", "tidal_artist": "Björk"}}
    a2t.save_cache(cache)
    reread = json.loads((state_dir / "matches.json").read_text(encoding="utf-8"))
    assert reread["q:bjork|joga"]["tidal_title"] == "Jóga"


# --------------------------------------------------------------- migration
def test_migrate_cache_reindexes_by_identity(mk_track, capsys):
    tracks = {
        "1": mk_track(tid="1", isrc="USUM71703861"),
        "2": mk_track(tid="2", name="Autre", artist="Autre"),
    }
    old = {"1": {"tidal_id": 111}, "2": {"tidal_id": 222}}
    new = a2t.migrate_cache(old, tracks)

    assert new["isrc:USUM71703861"] == {"tidal_id": 111}
    assert new[a2t.dedup_key(tracks["2"])] == {"tidal_id": 222}


def test_migrate_cache_is_idempotent(mk_track):
    tracks = {"1": mk_track(tid="1", isrc="USUM71703861")}
    already = {"isrc:USUM71703861": {"tidal_id": 111}}
    assert a2t.migrate_cache(already, tracks) is already


def test_migrate_cache_drops_entries_without_track(mk_track):
    """Un ID Apple absent de l'export courant ne peut plus etre reindexe : on l'oublie."""
    tracks = {"1": mk_track(tid="1")}
    out = a2t.migrate_cache({"1": {"tidal_id": 1}, "999": {"tidal_id": 9}}, tracks)
    assert list(out.values()) == [{"tidal_id": 1}]


def test_migrate_cache_collapses_duplicates(mk_track):
    """Deux IDs Apple pour le meme titre convergent vers une seule entree."""
    tracks = {"1": mk_track(tid="1", isrc="USUM71703861"),
              "2": mk_track(tid="2", isrc="USUM71703861")}
    out = a2t.migrate_cache({"1": {"tidal_id": 111}, "2": {"tidal_id": 111}}, tracks)
    assert out == {"isrc:USUM71703861": {"tidal_id": 111}}


def test_migrate_cache_empty():
    assert a2t.migrate_cache({}, {}) == {}
