"""Cache disque des matchs : lecture tolerante, ecriture atomique, migration."""

import json
from dataclasses import asdict

import apple2tidal as a2t


def test_load_cache_missing_file(state_dir):
    assert a2t.load_cache(state_dir / "matches.json") == {}


def test_save_then_load_roundtrip(state_dir):
    cache = {"isrc:USUM71703861": {"id": 123, "score": 100.0}}
    a2t.save_cache(cache, state_dir / "matches.json")
    assert a2t.load_cache(state_dir / "matches.json") == cache


def test_save_cache_leaves_no_temp_file(state_dir):
    a2t.save_cache({"q:a|b": {"id": None, "score": 0.0}}, state_dir / "matches.json")
    assert not (state_dir / "matches.tmp").exists()
    assert (state_dir / "matches.json").exists()


def test_load_cache_survives_corrupted_file(state_dir, capsys):
    """Un cache tronque (interruption brutale) ne doit pas faire planter le run."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "matches.json").write_text('{"a": {"tidal', encoding="utf-8")
    assert a2t.load_cache(state_dir / "matches.json") == {}
    assert "unreadable" in capsys.readouterr().out


def test_load_cache_empty_file(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "matches.json").write_text("   ", encoding="utf-8")
    assert a2t.load_cache(state_dir / "matches.json") == {}


def test_save_cache_handles_non_ascii(state_dir):
    cache = {"q:bjork|joga": {"id": 1, "title": "Jóga", "artist": "Björk"}}
    a2t.save_cache(cache, state_dir / "matches.json")
    reread = json.loads((state_dir / "matches.json").read_text(encoding="utf-8"))
    assert reread["q:bjork|joga"]["title"] == "Jóga"


# --------------------------------------------------------------- migration
def test_migrate_cache_reindexes_by_identity(mk_track, capsys):
    tracks = {
        "1": mk_track(tid="1", isrc="USUM71703861"),
        "2": mk_track(tid="2", name="Autre", artist="Autre"),
    }
    old = {"1": {"id": 111}, "2": {"id": 222}}
    new = a2t.migrate_cache(old, tracks)

    assert new["isrc:USUM71703861"] == {"id": 111}
    assert new[a2t.dedup_key(tracks["2"])] == {"id": 222}


def test_migrate_cache_is_idempotent(mk_track):
    tracks = {"1": mk_track(tid="1", isrc="USUM71703861")}
    already = {"isrc:USUM71703861": {"id": 111}}
    assert a2t.migrate_cache(already, tracks) is already


def test_migrate_cache_drops_entries_without_track(mk_track):
    """Un ID Apple absent de l'export courant ne peut plus etre reindexe : on l'oublie."""
    tracks = {"1": mk_track(tid="1")}
    out = a2t.migrate_cache({"1": {"id": 1}, "999": {"id": 9}}, tracks)
    assert list(out.values()) == [{"id": 1}]


def test_migrate_cache_collapses_duplicates(mk_track):
    """Deux IDs Apple pour le meme titre convergent vers une seule entree."""
    tracks = {"1": mk_track(tid="1", isrc="USUM71703861"),
              "2": mk_track(tid="2", isrc="USUM71703861")}
    out = a2t.migrate_cache({"1": {"id": 111}, "2": {"id": 111}}, tracks)
    assert out == {"isrc:USUM71703861": {"id": 111}}


def test_migrate_cache_empty():
    assert a2t.migrate_cache({}, {}) == {}


# ------------------------------------------- migration des champs tidal_*
def test_migrate_cache_renames_the_destination_fields(capsys):
    """Un cache ecrit quand TIDAL etait la seule destination : l'identifiant
    qu'il porte est celui de la destination courante, pas celui de TIDAL."""
    old = {"isrc:USUM71703861": {"tidal_id": 111, "tidal_title": "Song",
                                 "tidal_artist": "Artist", "score": 100.0,
                                 "album_id": 9, "threshold": 78.0}}
    new = a2t.migrate_cache(old, {})
    assert new["isrc:USUM71703861"] == {"id": 111, "title": "Song", "artist": "Artist",
                                        "score": 100.0, "album_id": 9, "threshold": 78.0}
    assert "renamed" in capsys.readouterr().out


def test_migrate_cache_keeps_a_cached_failure_a_failure():
    """tidal_id vaut None sur un echec : l'absence doit survivre au renommage,
    sans quoi needs_match relancerait 200 recherches deja faites."""
    out = a2t.migrate_cache({"q:a|b": {"tidal_id": None, "score": 40.0}}, {})
    assert out["q:a|b"] == {"id": None, "score": 40.0}


def test_migrate_cache_leaves_the_upc_entries_alone():
    """Les entrees `upc:` ne portent qu'un album_id : rien a renommer."""
    out = a2t.migrate_cache({"upc:00602537": {"album_id": 42}}, {})
    assert out == {"upc:00602537": {"album_id": 42}}


def test_migrate_cache_renames_and_reindexes_in_one_pass(mk_track):
    """Un cache assez vieux cumule les deux : indexe par ID Apple *et* tidal_*."""
    tracks = {"1": mk_track(tid="1", isrc="USUM71703861")}
    out = a2t.migrate_cache({"1": {"tidal_id": 111, "tidal_title": "Song"}}, tracks)
    assert out == {"isrc:USUM71703861": {"id": 111, "title": "Song"}}


def test_migrate_cache_says_nothing_when_there_is_nothing_to_rename(capsys):
    already = {"isrc:USUM71703861": {"id": 111, "score": 100.0}}
    assert a2t.migrate_cache(already, {}) is already
    assert capsys.readouterr().out == ""


# ----------------------------------------------------------------- seuil
def test_match_records_the_threshold_that_validated_it():
    """Sans ce champ, on ne sait pas relire une entree de cache a la main."""
    m = a2t.Match(id=1, score=81.0, threshold=78.0)
    assert asdict(m)["threshold"] == 78.0


def test_cached_match_is_replayed_when_the_threshold_goes_up():
    entry = {"id": 1, "score": 81.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=90.0, rematch=False) is True


def test_cached_match_is_kept_when_it_still_clears_the_threshold():
    entry = {"id": 1, "score": 95.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=90.0, rematch=False) is False


def test_isrc_match_survives_any_threshold():
    entry = {"id": 1, "score": 100.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=100.0, rematch=False) is False


def test_cached_failure_is_replayed_when_the_threshold_goes_down():
    """Meilleur candidat a 81, refuse a 90 : a 78 il passerait."""
    entry = {"id": None, "score": 81.0, "threshold": 90.0}
    assert a2t.needs_match(entry, threshold=78.0, rematch=False) is True


def test_cached_failure_stays_a_failure_at_the_same_threshold():
    entry = {"id": None, "score": 40.0, "threshold": 78.0}
    assert a2t.needs_match(entry, threshold=78.0, rematch=False) is False


def test_rematch_replays_the_failures_only():
    failure = {"id": None, "score": 40.0, "threshold": 78.0}
    success = {"id": 1, "score": 95.0, "threshold": 78.0}
    assert a2t.needs_match(failure, threshold=78.0, rematch=True) is True
    assert a2t.needs_match(success, threshold=78.0, rematch=True) is False


def test_absent_entry_is_always_searched():
    assert a2t.needs_match(None, threshold=78.0, rematch=False) is True


def test_entry_from_an_older_cache_is_judged_on_its_score():
    """Les caches d'avant ce champ n'ont pas de threshold : le score suffit a decider."""
    assert a2t.needs_match({"id": 1, "score": 81.0}, threshold=90.0, rematch=False) is True
    assert a2t.needs_match({"id": 1, "score": 95.0}, threshold=90.0, rematch=False) is False


# ------------------------------------------------------------- cache des UPC
def test_a_resolved_upc_is_remembered():
    """Une bibliotheque compte des centaines d'UPC et le catalogue TIDAL ne
    bouge pas d'une execution a l'autre : la deuxieme passe doit etre gratuite."""
    cache, calls = {}, []
    lookup = a2t.cached_upc_lookup(cache, lambda upc: calls.append(upc) or 42)
    assert lookup("886972394725") == 42
    assert lookup("886972394725") == 42
    assert calls == ["886972394725"]


def test_a_missing_upc_is_remembered_too():
    """C'est l'absence qui coute le plus cher : c'est elle qu'il faut memoriser."""
    cache, calls = {}, []
    lookup = a2t.cached_upc_lookup(cache, lambda upc: calls.append(upc) or None)
    assert lookup("000") is None
    assert lookup("000") is None
    assert calls == ["000"]


def test_the_upc_cache_has_its_own_key_prefix():
    cache = {}
    a2t.cached_upc_lookup(cache, lambda upc: 42)("886972394725")
    assert cache == {"upc:886972394725": {"album_id": 42}}


def test_rematch_retries_a_remembered_miss():
    """Un album ajoute au catalogue depuis la derniere passe doit pouvoir sortir."""
    cache = {"upc:000": {"album_id": None}, "upc:111": {"album_id": 7}}
    calls = []
    lookup = a2t.cached_upc_lookup(cache, lambda upc: calls.append(upc) or 9,
                                   rematch=True)
    assert lookup("000") == 9
    assert lookup("111") == 7      # un succes reste acquis
    assert calls == ["000"]


def test_migrate_cache_leaves_a_upc_only_cache_alone():
    """Sans ce prefixe, un cache ne contenant que des UPC passerait pour un
    vieux cache indexe par ID Apple, et serait vide de ses entrees."""
    cache = {"upc:886972394725": {"album_id": 42}}
    assert a2t.migrate_cache(cache, {}) is cache
