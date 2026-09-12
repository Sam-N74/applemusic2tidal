"""Les deux README gardent la meme structure.

Grossier, mais c'est le cas reel : on reecrit l'anglais, on oublie le francais,
et la version FR reste sur l'ancien plan pendant des mois sans que rien ne le
signale. Compter les titres de section suffit a l'attraper.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^(#{1,6}) +(.+?)\s*$", re.MULTILINE)


def headings(name: str) -> list[tuple[int, str]]:
    """Niveau et texte de chaque titre, hors blocs de code."""
    text = Path(ROOT / name).read_text(encoding="utf-8")
    outside_code = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return [(len(m.group(1)), m.group(2)) for m in HEADING.finditer(outside_code)]


@pytest.fixture(scope="module")
def both():
    return headings("README.md"), headings("README.fr.md")


def test_same_number_of_sections(both):
    en, fr = both
    assert len(en) == len(fr), (
        f"README.md a {len(en)} sections, README.fr.md en a {len(fr)} : "
        "une section a ete ajoutee ou retiree d'un seul cote"
    )


def test_same_section_levels(both):
    """Meme nombre de titres mais pas au meme niveau : la traduction a derive."""
    en, fr = both
    assert [level for level, _ in en] == [level for level, _ in fr]
