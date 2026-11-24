"""Find jobs links that open, job titles without board text, letters told one job at a time (newest first),
and saved PDFs whose words stay apart (fictional data)."""
import re

import pytest

from app import drafting, job_search, resume_render, selection
from app.bank import bank_profile
from tests.test_bank import SDET_JOB, with_bank

BODY = "I'm applying for this role.\n\nI'd welcome the chance to talk about the role."


def test_board_text_after_a_title_is_removed():
    t = "Principal QA Automation Developer (Platform) job details Quick apply for Principal QA Automation Developer"
    assert job_search.clean_title(t) == "Principal QA Automation Developer (Platform)"
    assert job_search.clean_title("QA Engineer - Apply now") == "QA Engineer"
    assert job_search.clean_title("Job Details Analyst") == "Job Details Analyst"         # a real title stays
    found = job_search.FoundJob(source="x", title=t, company="Example Corp", location="Canada", posted=None, url="u")
    assert found.title == "Principal QA Automation Developer (Platform)"


def test_a_jsearch_job_opens_the_employer_page_not_a_preview_that_refuses_browsers():
    dead = "https://ca.jobrapido.com/jobpreview/1"
    job = {"job_apply_link": dead, "job_apply_is_direct": False, "job_google_link": "https://www.google.com/x",
           "apply_options": [{"publisher": "Jobrapido", "apply_link": dead, "is_direct": False},
                             {"publisher": "Example Corp", "apply_link": "https://careers.example.com/1",
                              "is_direct": True}]}
    assert job_search._jsearch_link(job) == "https://careers.example.com/1"
    only_dead = {"job_apply_link": dead, "job_google_link": "https://www.google.com/x"}
    assert job_search._jsearch_link(only_dead) == "https://www.google.com/x"
    assert job_search._jsearch_link({"job_apply_link": "https://boards.example.com/2"}) == "https://boards.example.com/2"


def test_each_letter_example_is_one_job_and_the_newest_comes_first():
    p = with_bank()
    facts = drafting.build_facts(bank_profile(p))
    plan = selection.plan(p, SDET_JOB)
    priorities, _ = drafting.letter_examples(p, SDET_JOB, facts, plan)
    body, _ = drafting.arrange_letter(BODY, facts, p, SDET_JOB, priorities)
    paras = body.split("\n\n")[1:-1]
    assert paras
    from app.claims import employers_named
    starts = {e.company: e.start_date for e in p.experience}
    named = []
    for para in paras:
        if "personal project" in para:
            continue
        jobs = employers_named(para, p)
        if not jobs:                                                 # the same job as the example before
            assert named and para.startswith("I also "), para
            continue
        assert len(jobs) == 1, para                                  # never two employers in one example
        named.append(next(iter(jobs)))
    assert len(set(named)) == len(named)                             # a job is introduced once
    assert named == sorted(named, key=lambda c: starts[c], reverse=True)      # newest job first
    assert not re.search(r"\.\.", body)


def test_the_opening_names_the_clean_title_and_no_double_period():
    p = with_bank()
    facts = drafting.build_facts(bank_profile(p))
    posting = SDET_JOB.model_copy(update={"title": SDET_JOB.title + " job details Quick apply for " + SDET_JOB.title,
                                          "company": "Example Corp, Inc."})
    priorities, _ = drafting.letter_examples(p, posting, facts, selection.plan(p, posting))
    body, _ = drafting.arrange_letter(BODY, facts, p, posting, priorities)
    assert body.startswith(f"I'm applying for the {SDET_JOB.title} role at Example Corp, Inc.")
    assert "job details" not in body and "Inc.." not in body


def test_the_printed_page_never_narrows_the_space_between_words():
    # narrower word spacing makes PDF text readers (and ATS parsers) join words: "Jenkinsfailures"
    ws = re.search(r"WS = \[([^\]]*)\]", resume_render.SMART_JUSTIFY).group(1)
    assert all(float(x) >= 0 for x in ws.split(","))


@pytest.mark.exact_lines
def test_the_resume_uses_your_lines_exactly_and_never_repeats_a_summary_line(monkeypatch):
    import json
    from app import tailoring
    monkeypatch.setenv("JOB_AGENT_PAGE_MEASURE", "off")
    monkeypatch.setattr(tailoring, "EXACT_SUMMARY", True)

    class NoAI:
        def __getattr__(self, name):
            raise AssertionError("the AI was asked to write resume text")
    p = with_bank()
    content, _ = tailoring.tailor(NoAI(), p, SDET_JOB)
    r = json.loads(content)
    bank_lines = {x.text.strip() for x in p.experience_bank}
    bullets = [b["text"] for e in r["experience"] for b in e["bullets"]]
    assert bullets and all(any(t.startswith(b.rstrip(".")) for t in bank_lines) for b in bullets)
    sentences = re.split(r"(?<=[.!?])\s+", r["summary"])
    assert sentences[0] == re.split(r"(?<=[.!?])\s+", p.summary)[0]           # your own opening sentence
    for s in sentences[1:]:
        assert any(t.startswith(s.rstrip(".")) for t in bank_lines), s         # a whole line of yours
        assert s not in bullets                                                # never said twice on the page
