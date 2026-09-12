"""Transfert de bout en bout sur une destination en memoire.

C'est le test du contrat : le moteur ne connait que `providers.Destination`, et
`MemoryDestination` n'est qu'un adaptateur. Si un service reel demande de
retoucher engine.py ou matching.py, c'est le contrat qui est mal decoupe.
"""

import csv

import pytest
from doubles import MemoryDestination, track

import apple2tidal as a2t
from apple2tidal import state
from apple2tidal.model import Candidate


@pytest.fixture
def library():
    return a2t.Library(
        tracks={
            "1": track(tid="1", name="Song A", artist="Artist A", album="Album A",
                       isrc="USUM71703861"),
            "2": track(tid="2", name="Song B", artist="Artist B", album="Album B"),
            "3": track(tid="3", name="Nowhere", artist="Nobody", album="Nothing"),
            "4": track(tid="4", name="Song C", artist="Artist C", album="Album C", loved=True),
        },
        playlists=[a2t.Playlist(name="Road trip", track_ids=["1", "2", "3"])],
        albums=[a2t.Album(name="Album A", artist="Artist A", track_count=1, upc="123")],
    )


@pytest.fixture
def destination():
    return MemoryDestination(
        catalog=[
            Candidate(id=101, name="Song A", artists=["Artist A"], album="Album A",
                      album_id=900, duration_s=200),
            Candidate(id=102, name="Song B", artists=["Artist B"], album="Album B",
                      album_id=901, duration_s=200),
            Candidate(id=103, name="Song C", artists=["Artist C"], album="Album C",
                      album_id=902, duration_s=200),
        ],
        isrcs={"USUM71703861": 101},
        upcs={"123": 900},
    )


def test_a_full_transfer_needs_nothing_but_the_contract(state_dir, library, destination):
    store = a2t.Store(state_dir / "memory")
    opts = a2t.Options(playlists=True, favorites=True, albums=True, workers=2)

    cache = a2t.transfer(library, destination, store, opts)

    assert destination.playlists == {"Road trip": [101, 102]}
    assert destination.favorites == [101, 102, 103]
    assert destination.saved_albums == [900]
    # le cache est neutre : identite universelle en cle, ecrit sous le service
    assert cache["isrc:USUM71703861"]["id"] == 101
    assert cache["upc:123"] == {"album_id": 900}
    assert store.load_cache() == cache
    # le rapport liste le seul titre introuvable, avec sa playlist
    with open(state.REPORT_FILE, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(r["artist"], r["title"], r["playlists"]) for r in rows] == [("Nobody", "Nowhere", "Road trip")]


def test_a_second_run_costs_no_search(state_dir, library, destination):
    store = a2t.Store(state_dir / "memory")
    opts = a2t.Options(playlists=True, workers=1)
    a2t.transfer(library, destination, store, opts)
    assert destination.searches, "le premier passage doit chercher les titres sans ISRC"

    destination.searches.clear()
    destination.playlists.clear()
    a2t.transfer(library, destination, store, opts)

    assert destination.searches == []
    assert destination.playlists == {"Road trip": [101, 102]}


def test_loved_only_pushes_the_loved_tracks(state_dir, library, destination):
    a2t.transfer(library, destination, a2t.Store(state_dir / "memory"), a2t.Options(loved=True))
    assert destination.favorites == [103]
    assert destination.playlists == {}


def test_dry_run_matches_and_reports_but_writes_nothing(state_dir, library, destination):
    destination.dry = True
    store = a2t.Store(state_dir / "memory")
    a2t.transfer(library, destination, store, a2t.Options(playlists=True, favorites=True, dry_run=True))

    assert destination.playlists == {} and destination.favorites == []
    assert store.cache_file.exists()
    assert state.REPORT_FILE.exists()
