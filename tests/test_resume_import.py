"""Reading a resume into a profile preview. FICTIONAL resumes only."""

import base64
import io
import zipfile

from app.resume_import import extract_text, parse_resume
from app.schemas import Profile

RESUME = """Jordan Sample
jordan.sample@example.com | +1 (555) 010-0199 | linkedin.com/in/jordan-sample-fictional

PROFESSIONAL SUMMARY
Fictional QA engineer with 8 years of experience in API testing and test automation.

CORE SKILLS
Automation: Playwright, pytest, Selenium WebDriver
Cloud and tools: Docker, Kubernetes, GitHub
Actions

PROFESSIONAL EXPERIENCE
Contoso Health | Exampleville, ON Apr 2023 - Present
Senior QA Engineer
Built Playwright suites for a fictional patient portal, covering login, booking and payments across
three browsers.
Added API contract checks with pytest.
Northwind Bank | Sampletown, ON Jan 2019 - Mar 2023
QA Analyst
• Validated fictional ETL jobs with SQL reconciliation.
• Led defect triage for monthly releases.
Recognition: Fictional Quality Award, 2022.

PROJECTS
Weather Bot (personal project) | github.com/example/weather-bot
Built a fictional Telegram weather bot in Python.
Tech: Python, FastAPI

EDUCATION
Bachelor of Science in Computer Science | Example University (fictional) | Sep 2012 - Apr 2016

CERTIFICATIONS
Example Cloud Practitioner (fictional)
11/14/26, 9:51 PM Jordan Sample - Resume
localhost:8000/applications/2/resume 1/2
"""


def test_a_plain_text_resume_becomes_a_valid_profile():
    r = parse_resume(RESUME)
    p = r.profile
    Profile(**p)                                              # valid as-is
    assert p["full_name"] == "Jordan Sample"
    assert p["contact"]["email"] == "jordan.sample@example.com"
    assert p["contact"]["links"] == ["https://linkedin.com/in/jordan-sample-fictional"]
    assert p["summary"].startswith("Fictional QA engineer")
    assert p["skill_groups"][1] == {"name": "Cloud and tools", "skills": ["Docker", "Kubernetes", "GitHub Actions"]}
    first, second = p["experience"]
    assert (first["company"], first["title"], first["location"], first["start_date"]) == \
        ("Contoso Health", "Senior QA Engineer", "Exampleville, ON", "2023-04")
    assert "end_date" not in first
    assert first["highlights"] == [
        "Built Playwright suites for a fictional patient portal, covering login, booking and payments across "
        "three browsers.", "Added API contract checks with pytest."]       # wrapped line re-joined, no bullets needed
    assert second["end_date"] == "2023-03" and len(second["highlights"]) == 2
    assert second["recognition"] == "Fictional Quality Award, 2022."
    assert p["projects"][0]["name"] == "Weather Bot (personal project)"
    assert p["projects"][0]["link"] == "https://github.com/example/weather-bot"
    assert p["projects"][0]["skills"] == ["Python", "FastAPI"]
    assert p["education"][0]["institution"] == "Example University (fictional)"
    assert p["certifications"] == ["Example Cloud Practitioner (fictional)"]   # browser header/footer lines dropped
    assert r.warnings == []


def test_title_at_company_layout():
    text = ("Sam Example\nsam@example.com\nEXPERIENCE\nData Engineer at Globex (fictional), 2018 - 2021\n"
            "- Built fictional pipelines.\n")
    exp = parse_resume(text).profile["experience"][0]
    assert (exp["title"], exp["company"], exp["start_date"], exp["end_date"]) == \
        ("Data Engineer", "Globex (fictional)", "2018-01", "2021-12")


def test_docx_is_read_without_extra_packages():
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    paras = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in ["Jordan Sample", "EXPERIENCE",
                                                                             "Initech | Remote 2020 - Present",
                                                                             "Engineer", "Fixed TPS reports."])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {ns}><w:body>{paras}</w:body></w:document>")
    text = extract_text("cv.docx", buf.getvalue())
    assert "Initech | Remote 2020 - Present" in text
    assert parse_resume(text).profile["experience"][0]["highlights"] == ["Fixed TPS reports."]


def test_upload_endpoint_returns_a_preview_and_saves_nothing(client):
    b64 = base64.b64encode(RESUME.encode()).decode()
    r = client.post("/profile/resume", json={"filename": "resume.txt", "content_base64": b64})
    assert r.status_code == 200 and r.json()["profile"]["full_name"] == "Jordan Sample"
    assert client.get("/profile").status_code == 404                     # nothing saved
    assert client.post("/profile/resume", json={}).status_code == 422
    assert client.post("/profile/resume", json={"filename": "old.doc", "content_base64": "AAAA"}).status_code == 409
