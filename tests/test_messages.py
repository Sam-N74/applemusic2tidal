"""Internationalisation : parite des dictionnaires, resolution de la langue.

Le test de parite est le garde-fou du lot : sans lui, une chaine ajoutee en
anglais laisse un trou en francais que personne ne voit avant un utilisateur.
"""

import re
import string
from pathlib import Path

import pytest

import apple2tidal as a2t
from apple2tidal import messages
from apple2tidal.messages import EN, FR, resolve_lang, set_lang, t


def placeholders(template: str) -> set[str]:
    """Noms des champs {…} d'un gabarit, sans le format ni la conversion."""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


# ------------------------------------------------------------------- parite
def test_both_dictionaries_hold_the_same_keys():
    assert sorted(set(EN) ^ set(FR)) == []


def test_translations_keep_every_placeholder():
    """Un {n} perdu dans la traduction, c'est un KeyError au moment d'afficher."""
    drifted = {k for k in set(EN) & set(FR)
               if placeholders(EN[k]) != placeholders(FR[k])}
    assert sorted(drifted) == []


def test_no_translation_is_empty():
    blank = {k for table in (EN, FR) for k, v in table.items() if not v.strip()}
    assert sorted(blank) == []


def keys_used_by_the_package() -> set[str]:
    package = Path(a2t.__file__).parent
    # messages.py definit les cles, il ne les utilise pas : son texte est hors champ
    source = "\n".join(p.read_text(encoding="utf-8") for p in package.rglob("*.py")
                       if p.name != "messages.py")
    return set(re.findall(r"""\bt\(\s*["']([\w.]+)["']""", source))


def test_every_key_used_by_the_package_exists():
    """Empeche un t("cle.oubliee") d'atteindre l'utilisateur en KeyError."""
    used = keys_used_by_the_package()
    assert used, "aucun appel a t() trouve : le motif de recherche est a revoir"
    assert used <= set(EN)


def test_no_key_is_left_unused():
    """Une cle orpheline est du texte mort : on la supprime plutot que la traduire."""
    assert set(EN) - keys_used_by_the_package() == set()


# ------------------------------------------------------------------ t()
def test_t_returns_english_by_default():
    set_lang("en")
    assert t("main.done") == "Done."


def test_t_follows_the_selected_language():
    set_lang("fr")
    assert t("main.done") == "Terminé."


def test_t_interpolates_parameters():
    set_lang("en")
    assert t("favorites.added", n=12) == "  [ok] 12 tracks added to favorites"


def test_t_raises_on_unknown_key():
    set_lang("en")
    with pytest.raises(KeyError):
        t("cle.qui.nexiste.pas")


# ------------------------------------------------------- resolution de langue
def test_english_is_the_default():
    assert resolve_lang([], {}) == "en"


def test_environment_variable_is_honoured():
    assert resolve_lang([], {messages.ENV_VAR: "fr"}) == "fr"


@pytest.mark.parametrize("argv", [["--lang", "fr"], ["--lang=fr"],
                                  ["library.json", "--lang", "fr", "--all"]])
def test_lang_option_is_read_before_argparse(argv):
    assert resolve_lang(argv, {}) == "fr"


def test_lang_option_beats_the_environment():
    assert resolve_lang(["--lang", "en"], {messages.ENV_VAR: "fr"}) == "en"


@pytest.mark.parametrize("value", ["de", "", "  ", "français"])
def test_unknown_language_falls_back_to_english(value):
    assert resolve_lang(["--lang", value], {}) == "en"
    assert resolve_lang([], {messages.ENV_VAR: value}) == "en"


def test_language_name_is_case_insensitive():
    assert resolve_lang([], {messages.ENV_VAR: "FR"}) == "fr"


def test_set_lang_ignores_an_unknown_language():
    assert set_lang("de") == "en"


# --------------------------------------------------- mot de confirmation
@pytest.mark.parametrize("lang, word", [("en", "DELETE"), ("fr", "SUPPRIMER")])
def test_confirmation_accepts_the_translated_word(monkeypatch, lang, word):
    """Le mot suit la langue : un anglophone ne doit pas taper SUPPRIMER."""
    set_lang(lang)
    monkeypatch.setattr("builtins.input", lambda _: f"  {word} ")
    assert a2t.confirmed() is True


@pytest.mark.parametrize("lang, other", [("en", "SUPPRIMER"), ("fr", "DELETE")])
def test_confirmation_rejects_the_word_of_the_other_language(monkeypatch, lang, other):
    set_lang(lang)
    monkeypatch.setattr("builtins.input", lambda _: other)
    assert a2t.confirmed() is False


def test_confirmation_prompt_shows_the_word(monkeypatch):
    set_lang("fr")
    seen = []
    monkeypatch.setattr("builtins.input", lambda prompt: seen.append(prompt) or "")
    a2t.confirmed()
    assert "SUPPRIMER" in seen[0]
