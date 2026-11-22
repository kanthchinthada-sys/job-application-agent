"""Estimate how many printed pages a tailored resume takes (Letter, 10.5pt Times, the layout in resume_render).

Counted in text lines of the body font. The constants are calibrated against Chrome's print output
(tests/test_layout.py and scripts/calibrate_layout.py); the estimate errs slightly long, so a resume that
fits here fits on paper.
"""

import math
import re

CHARS_PER_LINE = 112        # full-width paragraph (summary, skills)
CHARS_PER_BULLET_LINE = 108  # bullets are indented
LINES_PER_PAGE = 54.5       # estimated lines per page when no browser can measure (cautious)


def _lines(text: str, width: int) -> int:
    return max(1, math.ceil(len(text) / width))


def estimate_lines(resume) -> float:
    total = 2.1 + 1 + 0.9                                    # name, contact line, rule
    total += 1.6 + _lines(resume.summary, CHARS_PER_LINE)    # heading + summary
    total += 1.6
    for g in resume.skill_groups or [{"name": "", "skills": resume.skills}]:
        total += _lines(f"{g['name']}: " + ", ".join(g["skills"]), CHARS_PER_LINE) + 0.25
    total += 1.6
    for r in resume.experience:
        total += 2 + 0.55                                    # company/dates line, title, gap
        total += sum(_lines(b.text, CHARS_PER_BULLET_LINE) + 0.08 for b in r.bullets)
        if r.recognition:
            total += _lines("Recognition: " + r.recognition, CHARS_PER_LINE) + 0.15
    if resume.projects:
        total += 1.6
        for p in resume.projects:
            total += 1 + 0.55
            items = p.get("highlights") or [p.get("description", "")]
            total += sum(_lines(h, CHARS_PER_BULLET_LINE) + 0.08 for h in items)
            if p.get("skills"):
                total += _lines("Tech: " + ", ".join(p["skills"]), CHARS_PER_BULLET_LINE)
    if resume.education:
        total += 1.6 + 2 * len(resume.education)
    if resume.certifications:
        total += 1.6 + len(resume.certifications)
    if resume.achievements:
        total += 1.6 + sum(_lines(a, CHARS_PER_LINE) for a in resume.achievements)
    return total


def estimate_pages(resume) -> float:
    return estimate_lines(resume) / LINES_PER_PAGE


# ---------------------------------------------------------------------------
# Fit a resume on N pages by removing the least important content first
# ---------------------------------------------------------------------------

MAX_PAGES = 2
SAFETY_LINES = 3                       # page breaks never fall exactly at the end of a page
# Default minimum bullets per role, most recent first. Each user sets their own in profile.resume_settings.
MIN_BULLETS = [6, 4, 3, 3, 2]
MIN_PROJECT_BULLETS = {"default": 3, "ai": 5}
HEADING_CAPS = [18, 14, 11, 9, 7]      # items per skill heading, tightened step by step
ROLE_WEIGHT = [1.0, 1.0, 0.9, 0.9, 0.9]     # above the minimums, the least relevant bullet goes first


def fit(resume, scores: dict[str, float] | None = None, ai_or_fde: bool = False,
        max_pages: int = MAX_PAGES, budget_override: float | None = None, min_bullets=None,
        protect: list[str] | None = None) -> dict:
    """Trim `resume` in place until it fits `max_pages`. Returns what was removed.

    Order: skills headings are tightened (named tools are kept longest), then the least relevant bullets go,
    weighed by role recency, never below each role's minimum. Nothing is reworded or invented.
    """
    scores = scores or {}
    # the job's keywords: skills and bullets that contain them are trimmed last (they are what an ATS looks for)
    keys = [k.lower() for k in (protect or []) if k.strip()]

    def has_key(text: str) -> int:
        from app.ats import resume_has            # the same test the ATS score uses
        return sum(1 for k in keys if resume_has(text, k))

    budget = budget_override if budget_override is not None else max_pages * LINES_PER_PAGE - SAFETY_LINES
    removed: list[str] = []
    trimmed_skills: list[str] = []

    def over() -> bool:
        return estimate_lines(resume) > budget

    def cap_skills(cap: int):
        from app.selection import _is_tool
        for g in resume.skill_groups:
            if len(g["skills"]) > cap:
                g["skills"].sort(key=lambda s: not has_key(s))       # stable: job keywords first
                # among those, a skill that adds a job keyword the resume doesn't show yet comes first;
                # one that only repeats a keyword ("API testing" next to "API and integration testing") after
                from app.ats import resume_has
                shown = " ".join([getattr(resume, "summary", "") or ""] + [b.text for r in resume.experience for b in r.bullets])
                covered = {k for k in keys if resume_has(shown, k)}
                first, later = [], []
                for s in g["skills"]:
                    mine = {k for k in keys if resume_has(s, k)}
                    (first if mine - covered else later).append(s)
                    covered |= mine
                g["skills"] = first + [s for s in later if has_key(s)] + [s for s in later if not has_key(s)]
                keep = g["skills"][:cap]
                # make room for named tools before generic phrases
                extra_tools = [s for s in g["skills"][cap:] if _is_tool(s)]
                generic = [s for s in keep if not _is_tool(s) and not has_key(s)]   # job keywords stay
                while extra_tools and generic:
                    keep.remove(generic.pop())
                    keep.append(extra_tools.pop(0))
                trimmed_skills.extend(s for s in g["skills"] if s not in keep)
                g["skills"] = keep

    def bullet_candidates():
        out = []
        for i, role in enumerate(resume.experience):
            mins = min_bullets or MIN_BULLETS
            floor = mins[min(i, len(mins) - 1)]
            if len(role.bullets) <= floor:
                continue
            w = ROLE_WEIGHT[min(i, len(ROLE_WEIGHT) - 1)]
            for j, b in enumerate(role.bullets):
                base = scores.get(b.source_id or "", 0.0) + 3.0 * has_key(b.text)
                out.append((base * w - 0.01 * j, ("role", i, j), b.source_id or b.text[:40]))
        for i, proj in enumerate(resume.projects):
            items = proj.get("highlights") or []
            floor = MIN_PROJECT_BULLETS["ai" if ai_or_fde else "default"]
            if len(items) <= floor:
                continue
            w = 1.1 if ai_or_fde else 0.6           # AI/FDE roles: the LLM project outranks older bullets
            for j, h in enumerate(items):
                base = scores.get(proj.get("highlight_ids", [None] * len(items))[j] or "", 0.0) if proj.get("highlight_ids") else 0.0
                base += 3.0 * has_key(h)
                out.append(((base or (len(items) - j)) * w - 0.01 * j, ("project", i, j), h[:40]))
        return out

    for cap in HEADING_CAPS[:2]:
        if not over():
            break
        cap_skills(cap)
    while over():
        cands = bullet_candidates()
        if not cands:
            break
        _, where, label = min(cands, key=lambda c: c[0])
        kind, i, j = where
        if kind == "role":
            resume.experience[i].bullets.pop(j)
        else:
            resume.projects[i]["highlights"].pop(j)
            if resume.projects[i].get("highlight_ids"):
                resume.projects[i]["highlight_ids"].pop(j)
        removed.append(label)
    for cap in HEADING_CAPS[2:]:
        if not over():
            break
        cap_skills(cap)
    return {"estimated_pages": round(estimate_lines(resume) / (budget + SAFETY_LINES) * max_pages, 2),
            "max_pages": max_pages, "fits": not over(), "removed_bullets": removed, "skills_left_out": trimmed_skills}


# ---------------------------------------------------------------------------
# Measure with a real browser when one is available (Playwright + Chromium, as used by the browser tests)
# ---------------------------------------------------------------------------

import copy
import os

PRINTED_LINES_PER_PAGE = 54.8          # Letter, 0.5in margins, 10.5pt x 1.25 line height
PAGE_BREAK_WASTE = 3.0                 # lines lost where pages break


def browser_measure(html: str) -> tuple[float, int] | None:
    """(printed body lines, PDF pages) from Chromium, or None if no browser is available.

    Runs in its own thread: Playwright's sync API refuses to start inside a thread that already runs it.
    """
    if os.environ.get("JOB_AGENT_PAGE_MEASURE", "auto") == "off":
        return None
    from app import browser as chromium

    def measure(b):
        page = chromium.print_page(b, html)
        try:
            height = page.evaluate("() => document.querySelector('[contenteditable]').getBoundingClientRect().height")
            pdf = page.pdf(format="Letter", prefer_css_page_size=True)
        finally:
            page.close()
        return height / (10.5 * 1.25 * 96 / 72), pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")
    try:
        return chromium.run(measure, timeout=60)      # one Chromium, kept open between measurements
    except Exception:            # no Chromium installed, sandbox issues, ...: fall back to the estimate
        return None


def fit_measured(resume, render_html, scores=None, ai_or_fde=False, max_pages: int = MAX_PAGES, min_bullets=None,
                 measure=browser_measure, protect: list[str] | None = None) -> dict:
    """Fit using the estimate, calibrated by one real measurement, then confirm with the browser.

    `render_html(resume) -> str`. Without a browser this is the same as `fit` (cautious estimate).
    """
    original = copy.deepcopy(resume)
    first = measure(render_html(resume))
    if first is None:
        report = fit(resume, scores, ai_or_fde, max_pages, min_bullets=min_bullets, protect=protect)
        report["measured"] = False
        return report
    real_lines, first_pages = first
    if first_pages == max_pages:
        report = {"estimated_pages": float(max_pages), "max_pages": max_pages, "removed_bullets": [],
                  "skills_left_out": [], "measured": True, "printed_pages": first_pages, "fits": True,
                  "measurements": 1}
        return report
    factor = real_lines / max(1.0, estimate_lines(resume))   # how this content prints vs the estimate
    capacity = max_pages * PRINTED_LINES_PER_PAGE - PAGE_BREAK_WASTE
    tries: dict[float, tuple] = {}

    def attempt_at(tighten: float, floors=min_bullets):
        attempt = copy.deepcopy(original)
        rep = fit(attempt, scores, ai_or_fde, max_pages, budget_override=(capacity - tighten) / factor,
                  min_bullets=floors, protect=protect)
        result = measure(render_html(attempt))
        tries[tighten] = (attempt, rep, result[1] if result else None)
        return tries[tighten]

    # Exactly `max_pages` pages: the FULLEST version that still fits. Binary search on how much to trim
    # (more trimming never makes it longer), each step measured in the browser.
    lo, hi = -20.0, 45.0                     # lo: keeps the most content; hi: trims hard
    best = None
    if attempt_at(hi)[2] is not None and tries[hi][2] <= max_pages:
        best = hi
        while hi - lo > 1.5:
            mid = round((lo + hi) / 2, 1)
            if (p := attempt_at(mid)[2]) is not None and p <= max_pages:
                best, hi = mid, mid
            else:
                lo = mid
        attempt, report, pages = tries[best]
    else:
        # Even hard trimming within the minimum bullets per role doesn't fit: go below the minimums, least
        # relevant first, and say so.
        attempt, report, pages = attempt_at(hi, floors=[3, 3, 2, 2, 2])
        report.setdefault("warnings", []).append(
            "Went below the minimum bullets per role to keep the resume on two pages.")
    resume.__dict__.update(attempt.__dict__)
    report.update({"measured": True, "printed_pages": pages, "fits": pages == max_pages,
                   "measurements": len(tries) + 1})
    return report
    real_lines, _ = first
    factor = real_lines / max(1.0, estimate_lines(resume))   # how this content prints vs the estimate
    capacity = max_pages * PRINTED_LINES_PER_PAGE - PAGE_BREAK_WASTE
    report = {}
    for tighten in (0, 3, 6, 10):
        attempt = copy.deepcopy(original)
        budget_lines = (capacity - tighten) / factor
        report = fit(attempt, scores, ai_or_fde, max_pages, budget_override=budget_lines)
        result = measure(render_html(attempt))
        pages = result[1] if result else None
        if pages is not None and pages <= max_pages:
            break
    resume.__dict__.update(attempt.__dict__)
    report.update({"measured": True, "printed_pages": pages, "fits": pages is not None and pages <= max_pages})
    return report
