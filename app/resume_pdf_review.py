"""Check the text that a parser can read from the app's actual PDF export."""

import re
import unicodedata

from pydantic import BaseModel

from app import resume_import, resume_match, resume_render
from app.tailoring import TailoredResume


class PdfCheck(BaseModel):
    readable: bool
    required_found: list[str]
    required_missing: list[str]
    issues: list[str]


def _compact(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return " ".join(text.translate(str.maketrans("–—−", "---")).split())


def check(data: bytes, resume: TailoredResume, coverage: resume_match.Coverage) -> PdfCheck:
    text = _compact(resume_import.extract_text("resume.pdf", data))
    issues = []
    if not text:
        issues.append("The PDF does not contain extractable text.")

    headings = ["Professional Summary", "Core Skills", "Professional Experience"]
    headings.extend(title for title, entries in (("Projects", resume.projects), ("Education", resume.education),
                    ("Certifications and Professional Development", resume.certifications),
                    ("Achievements", resume.achievements)) if entries)
    heading_pattern = r"(?<!\w)(" + "|".join(re.escape(title.upper()) for title in headings) + r")(?!\w)"
    heading_hits = list(re.finditer(heading_pattern, text))
    found_headings = list(dict.fromkeys(hit[0] for hit in heading_hits))
    missing_headings = [h for h in headings if h.upper() not in found_headings]
    if missing_headings:
        issues.append("Could not read these section headings: " + ", ".join(missing_headings) + ".")
    if found_headings != [h.upper() for h in headings if h not in missing_headings]:
        issues.append("Section headings came out in a different reading order.")

    experience = next((hit for hit in heading_hits if hit[0] == "PROFESSIONAL EXPERIENCE"), None)
    if experience:
        following = next((hit.start() for hit in heading_hits if hit.start() > experience.start()), len(text))
        roles_text = text[experience.end():following].casefold()
        position = 0
        for role in resume.experience:
            # Search for the visible employer/location and dates instead of repeating terms in a later bullet.
            employer = re.escape(_compact(role.company).casefold())
            if role.location:
                employer += r"\s*\|\s*" + re.escape(_compact(role.location).casefold())
            dates = f"{resume_render._month(role.start_date)} - {resume_render._month(role.end_date)}".casefold()
            title = re.escape(_compact(role.title).casefold())
            pattern = rf"(?<!\w){employer}\s+{re.escape(dates)}\s+{title}(?!\w)"
            hit = re.search(pattern, roles_text[position:])
            if hit:
                position += hit.end()
            else:
                issues.append(f"Could not confirm employer, dates, and title in the expected order for {role.company}.")

    required_found, required_missing = [], []
    for row in coverage.rows:
        if not row.required or row.placement not in resume_match._ON_RESUME:
            continue
        if resume_match._mentions(text, resume_match._terms(row.keyword), wording_only=True) or (
                row.evidence and _compact(row.evidence).casefold() in text.casefold()):
            required_found.append(row.keyword)
        else:
            required_missing.append(row.keyword)
    return PdfCheck(readable=bool(text), required_found=required_found, required_missing=required_missing,
                    issues=list(dict.fromkeys(issues)))
