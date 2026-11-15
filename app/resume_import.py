"""Read a resume (PDF, Word .docx, or plain text) into a profile ("resume_import_v1").

Rule-based, so it works without AI and never adds anything: every field comes from the resume's own text.
It understands the usual layout:

    NAME
    email | phone | links
    SUMMARY / PROFILE ...
    SKILLS: "Heading: a, b, c" lines (or a plain list)
    EXPERIENCE: "Company | Location   Mon YYYY - Present" (or "Title, Company, 2019 - 2021"), a title line,
                then bullets
    PROJECTS, EDUCATION, CERTIFICATIONS, ACHIEVEMENTS

The result is a PREVIEW: you review it and save it yourself. Anything it could not place is reported.
"""

import io
import re
import zipfile
from datetime import date
from xml.etree import ElementTree

from pydantic import BaseModel

from app.errors import ConflictError

METHOD = "resume_import_v1"

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ONE_DATE = rf"(?:{_MONTH}\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}}-\d{{2}}|\d{{4}})"
_RANGE = re.compile(rf"({_ONE_DATE})\s*(?:-|–|—|to)\s*({_ONE_DATE}|present|current|now|today)", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[•·▪◦●○■□➢➤►\-–—*]|\d+[.)])\s+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_LINK = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|[\w-]+\.(?:dev|io|me|com|ca|org))/[\w\-./?=&%#]*",
                   re.IGNORECASE)

SECTIONS = {
    "summary": ["summary", "professional summary", "profile", "about me", "about", "objective", "career summary",
                "professional profile", "overview"],
    "skills": ["skills", "core skills", "technical skills", "key skills", "core competencies", "competencies",
               "technologies", "tools", "skills and tools", "technical expertise", "areas of expertise"],
    "experience": ["experience", "professional experience", "work experience", "employment", "employment history",
                   "work history", "career history", "relevant experience"],
    "projects": ["projects", "personal projects", "selected projects", "side projects", "key projects"],
    "education": ["education", "education and training", "academic background", "qualifications"],
    "certifications": ["certifications", "certificates", "licenses", "licenses and certifications",
                       "certifications and professional development", "professional development", "training",
                       "courses"],
    "achievements": ["achievements", "awards", "honors", "honours", "awards and achievements", "accomplishments"],
}
_HEADING_OF = {h: key for key, names in SECTIONS.items() for h in names}
_DEGREE = re.compile(r"\b(bachelor|master|ph\.?d|doctor|diploma|associate|b\.?\s?sc|m\.?\s?sc|b\.?\s?tech|m\.?\s?tech|"
                     r"b\.?\s?e\b|m\.?\s?e\b|mba|b\.?a\b|m\.?a\b|certificate|degree)", re.IGNORECASE)


class ResumeImport(BaseModel):
    method: str = METHOD
    profile: dict                 # ready for PUT /profile after your review
    warnings: list[str]
    sections_found: list[str]


# ---------------------------------------------------------------------------
# File -> text
# ---------------------------------------------------------------------------

def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ConflictError("Reading PDF files needs the 'pypdf' package (pip install pypdf). "
                                "Or upload a .docx/.txt, or paste the text.") from exc
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if name.endswith(".docx") or data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ConflictError("This doesn't look like a Word .docx file.") from exc
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ElementTree.fromstring(xml)
        lines = []
        for para in root.iter(f"{ns}p"):
            text = "".join(t.text or "" for t in para.iter(f"{ns}t"))
            if para.find(f".//{ns}numPr") is not None and text.strip():
                text = "• " + text                     # Word list items are bullets
            lines.append(text)
        return "\n".join(lines)
    if name.endswith(".doc"):
        raise ConflictError("Old .doc files can't be read. Save it as .docx or PDF, or paste the text.")
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ConflictError("Could not read this file as text.")


# ---------------------------------------------------------------------------
# Text -> profile
# ---------------------------------------------------------------------------

def _ym(text: str, end: bool = False) -> str | None:
    t = text.strip().lower().rstrip(".")
    if t in ("present", "current", "now", "today"):
        return None
    m = re.match(rf"({_MONTH})\.?\s+(\d{{4}})", t)
    if m:
        return f"{m.group(2)}-{_MONTHS[m.group(1)[:3]]:02d}"
    m = re.match(r"(\d{1,2})/(\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{4})-(\d{2})", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"(\d{4})", t)
    if m:
        return f"{m.group(1)}-{'12' if end else '01'}"
    return None


def _heading(line: str) -> str | None:
    t = re.sub(r"[:\s]+$", "", line.strip()).lower()
    t = re.sub(r"\s+", " ", t.replace("&", "and"))
    if t in _HEADING_OF and len(line.strip()) <= 50:
        return _HEADING_OF[t]
    return None


def _clean(line: str) -> str:
    return " ".join(line.replace(" ", " ").split())


_JUNK = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}|(?:localhost|127\.0\.0\.1)[:/]|^page \d+( of \d+)?$|"
                   r"^\d+\s*/\s*\d+$", re.IGNORECASE)


def _is_header_like(line: str) -> bool:
    return bool(_RANGE.search(line) or "|" in line or _heading(line) or _LINK.search(line))


def _join_wrapped(lines: list[str]) -> list[str]:
    """Rebuild bullets: PDFs often drop the bullet characters and break long bullets across lines.

    A line continues the previous item unless the previous item ended a sentence (or was a heading-like line,
    or the job title right under a dated line), or the line itself is a bullet / dated / heading-like line.
    Items that came with a bullet character, or that start after a finished sentence, become "• " bullets.
    """
    out: list[str] = []
    after_dated = 0          # 1 right after a dated line: the next line is a title, not a bullet
    for line in lines:
        bullet = bool(_BULLET.match(line))
        text = _BULLET.sub("", line) if bullet else line
        prev = out[-1] if out else ""
        prev_done = prev.rstrip().endswith((".", "!", "?", ":", ";")) or _is_header_like(prev) or after_dated == 1
        if out and not bullet and not _is_header_like(line) and not prev_done and prev.startswith("• ") \
                or (out and not bullet and not _is_header_like(line) and line[:1].islower() and prev.startswith("• ")):
            out[-1] = prev + " " + text
            continue
        if _RANGE.search(line) or _is_header_like(line):
            out.append(text)
            after_dated = 1 if _RANGE.search(line) else 0
            continue
        if after_dated == 1 and not bullet:
            out.append(text)                       # the title under a dated line
            after_dated = 2
            continue
        if text.lower().startswith(("recognition:", "awards:", "award:", "tech:", "technologies:", "stack:")):
            out.append(text)
            after_dated = 0
            continue
        out.append("• " + text)
        after_dated = 0
    return out


def parse_resume(text: str) -> ResumeImport:
    lines = [_clean(x) for x in text.splitlines()]
    lines = [x for x in lines if x and not _JUNK.search(x)]
    warnings: list[str] = []
    if not lines:
        raise ConflictError("The resume is empty.")

    # --- header: name and contact (before the first section heading) ---
    first_section = next((i for i, x in enumerate(lines) if _heading(x)), len(lines))
    header, body = lines[:first_section], lines[first_section:]
    head_text = " | ".join(header)
    contact: dict = {}
    if m := _EMAIL.search(head_text):
        contact["email"] = m.group(0)
    phone = next((p.strip() for p in _PHONE.findall(head_text) if len(re.sub(r"\D", "", p)) >= 10), None)
    if phone:
        contact["phone"] = phone
    links = []
    for lm in _LINK.finditer(head_text):
        url = lm.group(0).rstrip(".,;|")
        if "@" in url:
            continue
        links.append(url if url.startswith("http") else "https://" + url)
    if links:
        contact["links"] = list(dict.fromkeys(links))[:10]
    name = next((x for x in header if not _EMAIL.search(x) and not _PHONE.search(x) and len(x.split()) <= 6
                 and not _LINK.search(x)), None)
    if not name:
        name = header[0] if header else lines[0]
        warnings.append("Could not find your name at the top; please check it.")
    name = re.split(r"\s*[|,]\s*", name)[0]
    headline = next((x for x in header[1:] if x != name and not _EMAIL.search(x) and not _PHONE.search(x)
                     and not _LINK.search(x) and len(x.split()) <= 12), None)

    # --- split the rest into sections ---
    sections: dict[str, list[str]] = {}
    current = None
    for x in body:
        h = _heading(x)
        if h:
            current = h
            sections.setdefault(h, [])
            continue
        if current:
            sections[current].append(x)
    profile: dict = {"full_name": name, "contact": contact}
    if headline:
        profile["headline"] = headline

    if s := sections.get("summary"):
        profile["summary"] = " ".join(_BULLET.sub("", x) for x in s)

    # --- skills ---
    groups, flat = [], []
    skill_lines: list[str] = []
    for x in sections.get("skills", []):
        if skill_lines and ":" in skill_lines[-1] and ":" not in x and not _BULLET.match(x) \
                and not skill_lines[-1].rstrip().endswith("."):
            skill_lines[-1] += " " + x                 # a skill line wrapped onto the next line
        else:
            skill_lines.append(x)
    for x in skill_lines:
        x = _BULLET.sub("", x)
        m = re.match(r"^([^:]{2,60}):\s*(.+)$", x)
        items_text = m.group(2) if m else x
        items = [i.strip(" .") for i in re.split(r",|;|\|| • |·", items_text) if i.strip(" .")]
        items = [i for i in items if 1 <= len(i) <= 60]
        if m and items:
            if groups and groups[-1]["name"] == m.group(1).strip():
                groups[-1]["skills"] += items
            else:
                groups.append({"name": m.group(1).strip(), "skills": items})
        flat += items
    if groups:
        profile["skill_groups"] = [{"name": g["name"], "skills": list(dict.fromkeys(g["skills"]))} for g in groups][:20]
    if flat:
        profile["skills"] = list(dict.fromkeys(flat))[:200]

    profile["experience"] = _experience(_join_wrapped(sections.get("experience", [])), warnings)
    profile["projects"] = _projects(_join_wrapped(sections.get("projects", [])))
    profile["education"] = _education(sections.get("education", []))
    certs = [_BULLET.sub("", x) for x in sections.get("certifications", [])]
    if certs:
        profile["certifications"] = [c for c in certs if len(c) <= 300][:50]
    ach = [_BULLET.sub("", x) for x in _join_wrapped(sections.get("achievements", []))]
    if ach:
        profile["achievements"] = ach[:100]

    if not profile["experience"]:
        warnings.append("No work experience was recognised. Check the EXPERIENCE section, or add it on the Profile page.")
    for key in ("summary", "skills", "experience"):
        if key not in sections:
            warnings.append(f"No {key.upper()} heading found.")
    return ResumeImport(profile=profile, warnings=warnings, sections_found=list(sections))


def _split_company_line(text: str) -> tuple[str, str | None]:
    """ "Northwind Bank | Toronto, ON" -> ("Northwind Bank", "Toronto, ON")."""
    parts = [p.strip(" ,-–") for p in re.split(r"\s+[|•·]\s+|\s+[–—-]\s+", text) if p.strip(" ,-–")]
    if not parts:
        return text.strip(), None
    return parts[0], (", ".join(parts[1:]) or None)


def _experience(lines: list[str], warnings: list[str]) -> list[dict]:
    roles: list[dict] = []
    i = 0
    while i < len(lines):
        x = lines[i]
        m = _RANGE.search(x) if not x.startswith("• ") else None
        if not m:
            if x.lower().startswith(("recognition:", "awards:", "award:")) and roles:
                roles[-1]["recognition"] = x.split(":", 1)[1].strip()
            elif x.startswith("• ") and roles:
                roles[-1]["highlights"].append(x[2:].strip())
            elif roles and not roles[-1]["title"] and not x.startswith("• "):
                roles[-1]["title"] = x
            elif roles and roles[-1]["highlights"] and not x.startswith("• "):
                roles[-1]["highlights"][-1] += " " + x
            i += 1
            continue
        start, end = _ym(m.group(1)), _ym(m.group(2), end=True)
        before = x[:m.start()].strip(" |,–-")
        after = x[m.end():].strip(" |,–-")
        title = ""
        company, location = _split_company_line(before or after)
        # "Senior Engineer, Northwind Bank" or "Senior Engineer at Northwind Bank"
        at = re.match(r"^(.+?)\s+(?:at|@)\s+(.+)$", company)
        if at:
            title, company = at.group(1), at.group(2)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not title and nxt and not nxt.startswith("• ") and not _RANGE.search(nxt):
            title = nxt
            i += 1
        # a title line ABOVE the dated line ("Senior Engineer" then "Northwind Bank | 2019 - 2021")
        if not title and roles and roles[-1]["_pending_title"]:
            title = roles[-1]["_pending_title"]
        if not start:
            warnings.append(f"Could not read the dates in: {x}")
            i += 1
            continue
        roles.append({"company": company or "Unknown company", "title": title, "location": location,
                      "start_date": start, "end_date": end, "highlights": [], "_pending_title": ""})
        i += 1
    out = []
    for r in roles:
        r.pop("_pending_title", None)
        if not r["title"]:
            r["title"] = "Role"
            warnings.append(f"No job title found for {r['company']}; please fill it in.")
        if r["end_date"] and r["end_date"] < r["start_date"]:
            r["end_date"] = None
        if not r["location"]:
            r.pop("location")
        if r["end_date"] is None:
            r.pop("end_date")
        out.append(r)
    return out[:50]


def _projects(lines: list[str]) -> list[dict]:
    projects: list[dict] = []
    for x in lines:
        item = x[2:].strip() if x.startswith("• ") else x
        m = re.match(r"^(?:tech|technologies|stack|tools)\s*:\s*(.+)$", item, re.IGNORECASE)
        if m and projects:
            projects[-1]["skills"] = [s.strip() for s in m.group(1).split(",") if s.strip()][:50]
            continue
        looks_like_name = len(item.split()) <= 8 and not item.endswith((".", "!", "?"))
        if x.startswith("• ") and projects and not (looks_like_name and projects[-1]["highlights"]):
            projects[-1]["highlights"].append(item)
            continue
        x = item
        name, link = _split_company_line(x)
        url = None
        lm = _LINK.search(x)
        if lm:
            url = lm.group(0) if lm.group(0).startswith("http") else "https://" + lm.group(0)
            name = name if not _LINK.search(name) else (link or name)
        projects.append({"name": name[:300], "highlights": [], **({"link": url} if url else {})})
    for p in projects:
        p["description"] = p["highlights"][0] if p["highlights"] else p["name"]
    return projects[:50]


def _education(lines: list[str]) -> list[dict]:
    out: list[dict] = []
    for x in lines:
        x = _BULLET.sub("", x)
        m = _RANGE.search(x)
        dates = (_ym(m.group(1)), _ym(m.group(2), end=True)) if m else (None, None)
        text = (x[:m.start()] + x[m.end():]).strip(" |,–-") if m else x
        if _DEGREE.search(text):
            parts = [p.strip() for p in re.split(r"\s+[|•·]\s+|,\s+(?=[A-Z])", text) if p.strip()]
            degree = next((p for p in parts if _DEGREE.search(p)), parts[0])
            institution = next((p for p in parts if p != degree), None)
            out.append({"degree": degree, "institution": institution or "(institution)",
                        **({"start_date": dates[0]} if dates[0] else {}),
                        **({"end_date": dates[1]} if dates[1] else {})})
        elif out and "institution" in out[-1] and out[-1]["institution"] == "(institution)":
            out[-1]["institution"] = text
        elif out and m and "start_date" not in out[-1]:
            if dates[0]:
                out[-1]["start_date"] = dates[0]
            if dates[1]:
                out[-1]["end_date"] = dates[1]
            loc = text.strip()
            if loc:
                out[-1]["location"] = loc
    return out[:20]


def today_ym() -> str:
    return date.today().strftime("%Y-%m")
