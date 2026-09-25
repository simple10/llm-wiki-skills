"""The stage sandboxes the package ships: each harvest stage's `sandbox_ref`
resolves to one reference here, the reference's snippet reaches a model, and
a credentialed unit can spend its credential somewhere. That the snippet's
keys are admitted is `sandboxes enable`'s to say, in test_install.py."""

from __future__ import annotations

import re

import pytest

from harness import ROOT, SKILLS, SOURCE, jsonc, snippet, unit_manifest

MODEL = {"api.anthropic.com", "api.openai.com", "chatgpt.com"}
REFERENCES = ROOT / "references" / "sandboxes"

# A unit's harvest stage MAY carry no `sandbox_ref` at all — the ninth unit's
# `script` stage runs the seeded `harvest` sandbox instead (A-5) — so only a
# unit that DOES name one has a reference to check here.
SANDBOXED = [n for n in SKILLS if "sandbox_ref" in unit_manifest(n)["stages"].get("harvest", {})]


def _reference(name: str) -> tuple[str, dict]:
    ref = unit_manifest(name)["stages"]["harvest"]["sandbox_ref"]
    package, rel = ref.rsplit(":", 1)
    assert package == SOURCE, f"{name}: {ref} names a package other than this one"
    path = REFERENCES / f"{rel}.md"
    assert path.is_file(), f"{name}: {ref} resolves to {path.relative_to(ROOT)}, which does not exist"
    return rel, jsonc(snippet(path.read_text(encoding="utf-8")))


def _hosts(name: str) -> list[str]:
    return [k.removeprefix("host:") for k in unit_manifest(name)["keywords"] if k.startswith("host:")]


@pytest.mark.parametrize("name", SKILLS)
def test_only_harvest_names_a_sandbox_and_requires_names_no_network(name):
    manifest = unit_manifest(name)
    assert "network" not in manifest["requires"], name
    for stage, spec in manifest["stages"].items():
        assert stage == "harvest" or "sandbox_ref" not in spec, (name, stage, spec)
        assert not {"sandbox", "reviewed"} & set(spec), f"{name}: a package manifest carries no binding"


@pytest.mark.parametrize("name", SANDBOXED)
def test_the_reference_is_the_units_venue_and_its_snippet_is_one_policy(name):
    rel, doc = _reference(name)
    venue = unit_manifest(name)["venue"]
    assert rel == f"{venue}/{venue}.harvest", rel
    assert set(doc) == {"v", "profile"} and doc["v"] == 2, doc
    allow = doc["profile"]["network"]["allow_domain"]
    assert MODEL <= set(allow), f"{name}: the snippet lacks a model endpoint"


@pytest.mark.parametrize("name", [n for n in SKILLS if unit_manifest(n)["requires"].get("credential")])
def test_a_credentialed_unit_claims_an_exact_host_its_snippet_reaches(name):
    _, doc = _reference(name)
    claims = [h for h in _hosts(name) if not h.startswith("*")]
    assert set(claims) & set(doc["profile"]["network"]["allow_domain"]), f"{name}: no exact host: claim is reached, so dispatch refuses a job with no host"


def test_every_reference_is_one_a_unit_names():
    named = {_reference(n)[0] for n in SANDBOXED}
    shipped = {str(p.relative_to(REFERENCES).with_suffix("")) for p in REFERENCES.rglob("*.md")}
    assert shipped == named, shipped ^ named


def test_the_snippet_reader_skips_comments_and_keeps_urls():
    assert jsonc('{"a": "https://x.example/y", // gone\n "b": 1}') == {"a": "https://x.example/y", "b": 1}
    assert re.fullmatch(r"\{\n\}\n", snippet("x\n```jsonc\n{\n}\n```\n"))
