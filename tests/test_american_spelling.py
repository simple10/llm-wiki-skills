"""American English across what this package ships and what tests it.

Mirrors the plugins repo's gate: a whole-word match, so `Licence Ltd` in a
venue's own name would have to be quoted around, not spelled British by
accident.
"""

from __future__ import annotations

import re

import pytest

from conftest import ROOT

BRITISH = (
    "behaviour", "colour", "licence", "recognise", "recognised", "recognises",
    "organise", "organised", "organisation", "analyse", "analysed", "honour",
    "honours", "honoured", "initialise", "normalise", "serialise", "optimise",
    "customise", "synchronise", "summarise", "catalogue", "centre", "cancelled",
    "modelling", "labelled", "travelling", "whilst", "amongst", "defence",
    "programme", "practise", "authorise", "apologise", "fulfil", "grey",
)
PATTERN = re.compile(r"(?<![\w-])(" + "|".join(BRITISH) + r")(?![\w-])", re.I)
FILES = sorted(  # this file spells them all, on purpose
    [
        *(p for tree in ("skills", "tests", "scripts", ".github") for p in (ROOT / tree).rglob("*")
          if p.is_file() and p.suffix in {".md", ".py", ".json", ".yml", ".yaml"}
          and "__pycache__" not in p.parts and p.name != "test_american_spelling.py"),
        ROOT / "README.md",
        ROOT / "llm-wiki-package.json",
    ]
)


def test_the_scan_finds_the_word_it_was_written_for():
    assert PATTERN.search("the script honours it") and not PATTERN.search("the script honors it")


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_british_spelling(path):
    found = [f"{path.relative_to(ROOT)}:{n}: {m.group(1)}"
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             for m in [PATTERN.search(line)] if m]
    assert not found, "\n".join(found)
