"""The evaluation harness itself, run against a fake model."""

from app.evaluation import run, score_extraction


def test_score_extraction_fuzzy_matching():
    s = score_extraction(["Java", "REST Assured", "JUnit", "Cooking"], ["Java", "REST Assured", "JUnit 5", "SQL"])
    assert s["recall"] == 0.75 and s["precision"] == 0.75
    assert s["missed"] == ["SQL"] and s["extra"] == ["Cooking"]


def test_run_produces_summary(fake_llm):
    def answer(system, user, schema):
        fake_llm.calls.append(1)
        if "text" in schema["properties"]:
            return {"text": "I build pytest suites for a payments API.", "fact_ids_used": ["F4"]}
        return {"required_skills": ["Python", "Docker", "Java"], "preferred_skills": []}
    fake_llm.generate_json = answer
    report = run(fake_llm)
    s = report["summary"]
    assert s["cases"] == 3 and s["letters_structured_correctly"] == 3
    assert 0 < s["extraction_recall"] <= 1
    assert all(r["cover_letter"]["signed_correctly"] for r in report["results"])


def test_short_skill_names_match():
    assert score_extraction(["k6 performance testing", "Go"], ["k6", "Go"])["recall"] == 1.0


# ---------- robustness: repeated runs, prompt versions, baseline comparison ----------

from app import evaluation  # noqa: E402


def test_repeated_runs_separate_random_from_systematic_misses(fake_llm):
    fake_llm.queue = [
        {"required_skills": ["Python", "SQL"], "preferred_skills": []},
        {"required_skills": ["Python"], "preferred_skills": []},
        {"required_skills": ["Python", "SQL"], "preferred_skills": []},
    ]
    r = evaluation.repeated_runs(fake_llm, "Python and SQL and Kubernetes (fictional)", ["Python", "SQL", "Kubernetes"], n=3)
    assert r["systematic_misses"] == ["Kubernetes"]          # missed every time
    assert r["random_misses"] == ["SQL"]                     # missed once
    assert r["unstable_skills"] == ["sql"] and 0 < r["stability"] < 1


def test_prompt_version_changes_when_a_prompt_changes(monkeypatch):
    from app import drafting
    before = evaluation.prompt_version()
    monkeypatch.setattr(drafting, "SYSTEM_PROMPT", drafting.SYSTEM_PROMPT + "\nExtra rule.")
    assert evaluation.prompt_version() != before


def test_compare_lists_only_real_regressions():
    base = {"model": "a", "prompt_version": "x", "extraction_recall": 0.8, "extraction_precision": 0.8,
            "letters_with_zero_warnings": 2, "unflagged_leaks": 0, "stability": 0.9, "avg_humanizer_score": 95}
    same_ish = {**base, "extraction_recall": 0.77, "prompt_version": "y"}
    worse = {**base, "extraction_recall": 0.6, "letters_with_zero_warnings": 1, "unflagged_leaks": 1, "stability": 0.7}
    assert evaluation.compare(base, same_ish) == []
    problems = evaluation.compare(base, worse)
    assert len(problems) == 4 and any(p.startswith("unflagged_leaks") for p in problems)


def test_full_run_summary_has_robustness_metrics(fake_llm):
    fake_llm.answer = {"required_skills": ["Python"], "preferred_skills": [], "text": "I build pytest suites.",
                       "fact_ids_used": ["F4"]}
    report = evaluation.run_all(fake_llm, repeat=2)
    s = report["summary"]
    for key in ["prompt_version", "stability", "average_consistency", "letters_with_leaks", "unflagged_leaks"]:
        assert key in s
    assert report["adversarial"]["cases"] == 3
