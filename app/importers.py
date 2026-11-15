"""Import jobs from public job-board APIs (Greenhouse, Lever, Ashby).

These boards publish open postings through free, public, read-only APIs that
are meant to be consumed by other sites. We only READ postings; nothing is
submitted anywhere.

Supported links
---------------
Greenhouse  boards.greenhouse.io/<board>/jobs/<id>, job-boards.greenhouse.io/<board>/jobs/<id>,
            my.greenhouse.io/jobs/<board>/<id>, boards.greenhouse.io/embed/job_app?for=<board>&token=<id>
Lever       jobs.lever.co/<company>/<posting-id>
Ashby       jobs.ashbyhq.com/<org>/<job-id>
A board link without a job id (e.g. jobs.lever.co/<company>) lists all postings.

``Fetcher`` is injectable so tests never touch the network.
"""

import re
from collections.abc import Callable
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel

from app.errors import ConflictError, NotFoundError

Fetcher = Callable[[str], object]   # url -> parsed JSON


class ImportError_(ConflictError):
    """The link is not supported or the board answered unexpectedly -> HTTP 409."""


def http_fetch(url: str) -> object:
    try:
        response = httpx.get(url, timeout=20, headers={"User-Agent": "job-application-agent (personal use)"})
    except httpx.HTTPError as exc:
        raise ImportError_(f"Could not reach {urlparse(url).netloc}: {exc.__class__.__name__}") from exc
    if response.status_code == 404:
        raise NotFoundError("That job or board was not found. It may have been closed.")
    if response.status_code != 200:
        raise ImportError_(f"The job board answered HTTP {response.status_code}.")
    return response.json()


class ImportedJob(BaseModel):
    """A posting ready to review; nothing is saved until you POST /jobs."""

    provider: str
    title: str
    company: str
    location: str | None
    source_url: str
    description: str
    employment_type: str | None = None


class BoardPosting(BaseModel):
    provider: str
    title: str
    company: str
    location: str
    source_url: str
    posted: str | None = None        # YYYY-MM-DD when the board says (first published / created)


def _posted(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, tz=timezone.utc).date().isoformat()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
    return m.group(1) if m else None


class BoardLink(BaseModel):
    provider: str
    board: str
    job_id: str | None


# ---------------------------------------------------------------------------
# HTML -> readable text
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "ul", "ol", "tr"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(unescape(html))   # Greenhouse double-escapes its HTML
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------------------
# Link parsing
# ---------------------------------------------------------------------------

def parse_link(url: str) -> BoardLink:
    parsed = urlparse(url.strip() if "://" in url else "https://" + url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and parts and parts[0] == "embed" \
            and query.get("for"):
        # embedded form: boards.greenhouse.io/embed/job_app?for=<board>&token=<id>
        return BoardLink(provider="greenhouse", board=query["for"][0], job_id=(query.get("token") or [None])[0])
    if host == "my.greenhouse.io" and len(parts) >= 3 and parts[0] == "jobs":
        # Greenhouse's job-seeker site: my.greenhouse.io/jobs/<board>/<id>?<search filters>
        return BoardLink(provider="greenhouse", board=parts[1], job_id=parts[2])
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and parts:
        job_id = parts[2] if len(parts) >= 3 and parts[1] == "jobs" else None
        return BoardLink(provider="greenhouse", board=parts[0], job_id=job_id)
    if host == "jobs.lever.co" and parts:
        return BoardLink(provider="lever", board=parts[0], job_id=parts[1] if len(parts) > 1 else None)
    if host == "jobs.ashbyhq.com" and parts:
        return BoardLink(provider="ashby", board=parts[0], job_id=parts[1] if len(parts) > 1 else None)
    raise ImportError_(
        "This link isn't supported yet. Supported: Greenhouse (boards.greenhouse.io), Lever (jobs.lever.co) "
        "and Ashby (jobs.ashbyhq.com). For other sites, paste the description on the Add job tab."
    )


def _pretty_company(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_]", slug))


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _greenhouse_location(job: dict) -> str:
    places = [job.get("location", {}).get("name")]
    places += [o.get("location") or o.get("name") for o in job.get("offices", [])]
    for m in job.get("metadata") or []:
        if "location" in (m.get("name") or "").lower():
            value = m.get("value")
            places += value if isinstance(value, list) else [value]
    seen, out = set(), []
    for p in places:
        if p and str(p).lower() not in seen:
            seen.add(str(p).lower())
            out.append(str(p))
    return "; ".join(out)


def _ashby_location(job: dict) -> str:
    places = [job.get("location")] + [s.get("location") for s in job.get("secondaryLocations") or []]
    text = "; ".join(p for p in places if p)
    if job.get("isRemote") and "remote" not in text.lower():
        text += "; Remote"
    return text


def fetch_job(link: BoardLink, fetch: Fetcher = http_fetch) -> ImportedJob:
    if not link.job_id:
        raise ImportError_("That is a board link. Use the board search to pick a job, or paste a job's own link.")
    if link.provider == "greenhouse":
        job = fetch(f"https://boards-api.greenhouse.io/v1/boards/{link.board}/jobs/{link.job_id}")
        return ImportedJob(
            provider="greenhouse", title=job["title"].strip(),
            company=job.get("company_name") or _pretty_company(link.board),
            location=_greenhouse_location(job) or None, source_url=job.get("absolute_url") or "",
            description=html_to_text(job.get("content") or ""),
        )
    if link.provider == "lever":
        job = fetch(f"https://api.lever.co/v0/postings/{link.board}/{link.job_id}?mode=json")
        sections = [job.get("descriptionPlain") or ""]
        for lst in job.get("lists") or []:
            sections.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content') or '')}")
        sections.append(job.get("additionalPlain") or "")
        cats = job.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location")]
        workplace = job.get("workplaceType")
        location = "; ".join(x for x in locations if x) + (f" ({workplace})" if workplace else "")
        return ImportedJob(
            provider="lever", title=job["text"].strip(), company=_pretty_company(link.board),
            location=location or None, source_url=job.get("hostedUrl") or "",
            description="\n\n".join(s.strip() for s in sections if s.strip()),
            employment_type=cats.get("commitment"),
        )
    if link.provider == "ashby":
        board = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{link.board}")
        job = next((j for j in board.get("jobs", []) if j.get("id") == link.job_id), None)
        if job is None:
            raise NotFoundError("That Ashby job was not found on the board. It may have been closed.")
        return ImportedJob(
            provider="ashby", title=job["title"].strip(), company=_pretty_company(link.board),
            location=_ashby_location(job) or None, source_url=job.get("jobUrl") or "",
            description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or ""),
            employment_type=job.get("employmentType"),
        )
    raise ImportError_(f"Unknown provider {link.provider}")


def list_board(link: BoardLink, fetch: Fetcher = http_fetch) -> list[BoardPosting]:
    if link.provider == "greenhouse":
        data = fetch(f"https://boards-api.greenhouse.io/v1/boards/{link.board}/jobs")
        return [BoardPosting(provider="greenhouse", title=j["title"].strip(),
                             company=j.get("company_name") or _pretty_company(link.board),
                             location=_greenhouse_location(j), source_url=j.get("absolute_url") or "",
                             posted=_posted(j.get("first_published") or j.get("updated_at")))
                for j in data.get("jobs", [])]
    if link.provider == "lever":
        data = fetch(f"https://api.lever.co/v0/postings/{link.board}?mode=json")
        out = []
        for j in data:
            cats = j.get("categories") or {}
            locs = cats.get("allLocations") or [cats.get("location")]
            wp = j.get("workplaceType")
            out.append(BoardPosting(provider="lever", title=j["text"].strip(), company=_pretty_company(link.board),
                                    location="; ".join(x for x in locs if x) + (f" ({wp})" if wp else ""),
                                    source_url=j.get("hostedUrl") or "", posted=_posted(j.get("createdAt"))))
        return out
    if link.provider == "ashby":
        data = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{link.board}")
        return [BoardPosting(provider="ashby", title=j["title"].strip(), company=_pretty_company(link.board),
                             location=_ashby_location(j), source_url=j.get("jobUrl") or "",
                             posted=_posted(j.get("publishedAt")))
                for j in data.get("jobs", []) if j.get("isListed", True)]
    raise ImportError_(f"Unknown provider {link.provider}")
