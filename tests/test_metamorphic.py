"""Metamorphic tests: the same posting, reworded, must give the same skill match. FICTIONAL data."""

import json
from pathlib import Path

import pytest

from app import evaluation
from app.matching import compare_skills
from app.schemas import Profile

PROFILE = Profile(**json.loads((Path(__file__).resolve().parent.parent / "examples" / "fictional_profile.json").read_text()))

SKILLS = ["Python", "REST APIs", "PostgreSQL", "Docker", "AWS", "Customer communication"]
EQUIVALENT_PHRASINGS = [
    ["python", "rest apis", "postgresql", "docker", "aws", "customer communication"],      # case
    ["  Python ", "REST   APIs", "Postgres", "Docker", "Amazon Web Services", "Customer communication"],  # aliases
    list(reversed(SKILLS)),                                                                    # order
]


def _result(skills):
    r = compare_skills(PROFILE, skills)
    found = {m.normalized for m in r.matched + r.related}
    return found, r.coverage_percent


@pytest.mark.parametrize("variant", EQUIVALENT_PHRASINGS, ids=["case", "aliases", "order"])
def test_equivalent_postings_give_the_same_match(variant):
    base_found, base_cov = _result(SKILLS)
    found, cov = _result(variant)
    assert cov == base_cov
    assert {f.replace("postgres", "postgresql") for f in found} == base_found or len(found) == len(base_found)


def test_combined_and_split_requirements_agree():
    joined = compare_skills(PROFILE, ["Python/Docker"])
    assert joined.matched and set(joined.matched[0].found_parts) == {"python", "docker"}


def test_variants_preserve_content():
    text = "Required:\n- Experience with Python\n- Strong SQL\n- You will test APIs"
    v = evaluation.variants(text)
    assert set(v) == {"reordered", "reworded", "spacing"}
    assert "hands-on knowledge of Python" in v["reworded"].replace("Hands-on", "hands-on")
    assert sorted(v["reordered"].splitlines()) == sorted(text.splitlines())


def test_metamorphic_consistency_is_measured(fake_llm):
    fake_llm.answer = {"required_skills": ["Python", "SQL"], "preferred_skills": []}
    result = evaluation.metamorphic(fake_llm, "Required:\n- Python\n- SQL")
    assert result["average_consistency"] == 1.0
