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


# ----------------------------------------------------------------- seuil
def test_match_records_the_threshold_that_validated_it():
    """Sans ce champ, on ne sait pas relire une entree de cache a la main."""
    m = a2t.Match(tidal_id=1, score=81.0, threshold=78.0)
    assert a2t.asdict(m)["threshold"] == 78.0


def test_cached_match_is_replayed_when_the_threshold_goes_up():
    entry = {"tidal_id": 1, "score": 81.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=90.0, rematch=False) is True


def test_cached_match_is_kept_when_it_still_clears_the_threshold():
    entry = {"tidal_id": 1, "score": 95.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=90.0, rematch=False) is False


def test_isrc_match_survives_any_threshold():
    entry = {"tidal_id": 1, "score": 100.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=100.0, rematch=False) is False


def test_cached_failure_is_replayed_when_the_threshold_goes_down():
    """Meilleur candidat a 81, refuse a 90 : a 78 il passerait."""
    entry = {"tidal_id": None, "score": 81.0, "threshold": 90.0}
    assert a2t.needs_match(entry, threshold=78.0, rematch=False) is True


def test_cached_failure_stays_a_failure_at_the_same_threshold():
    entry = {"tidal_id": None, "score": 40.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=78.0, rematch=False) is False


def test_rematch_replays_the_failures_only():
    failure = {"tidal_id": None, "score": 40.0, "threshold": 78.0}
    success = {"tidal_id": 1, "score": 95.0, "threshold": 78.0}
    assert a2t.needs_match(failure, threshold=78.0, rematch=True) is True
    assert a2t.needs_match(success, threshold=78.0, rematch=True) is False


def test_absent_entry_is_always_searched():
    assert a2t.needs_match(None, threshold=78.0, rematch=False) is True


def test_entry_from_an_older_cache_is_judged_on_its_score():
    """Les caches d'avant ce champ n'ont pas de threshold : le score suffit a decider."""
    assert a2t.needs_match({"tidal_id": 1, "score": 81.0}, threshold=90.0, rematch=False) is True
    assert a2t.needs_match({"tidal_id": 1, "score": 95.0}, threshold=90.0, rematch=False) is False
