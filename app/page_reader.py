"""Read a job posting from its web page ("page_reader_v1").

Most job sites publish each posting as structured data for search engines: a schema.org ``JobPosting`` block
(JSON-LD) with the title, company, location and the FULL description. This module reads that block, so a link
from Adzuna, a company careers site, Workable, SmartRecruiters and many others gives the complete posting.

Only that one public page is read, when you add or apply to the job (never in bulk). Pages without the block
are not guessed at: the caller then asks you to paste the description.
"""

import json
import re
from collections.abc import Callable
from html import unescape
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.errors import ConflictError

PageFetcher = Callable[[str], str]          # url -> HTML text

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/130.0 Safari/537.36 job-application-agent")


class PagePosting(BaseModel):
    title: str
    company: str | None
    location: str | None
    description: str
    url: str


def http_get_page(url: str) -> str:
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True,
                      headers={"User-Agent": _BROWSER_UA, "Accept": "text/html,application/xhtml+xml"})
    except httpx.HTTPError as exc:
        raise ConflictError(f"Could not reach {urlparse(url).netloc}: {exc.__class__.__name__}") from exc
    if r.status_code != 200:
        raise ConflictError(f"{urlparse(url).netloc} answered HTTP {r.status_code}.")
    return r.text


def _blocks(html: str) -> list:
    out = []
    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(unescape(raw))
            except json.JSONDecodeError:
                continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                out.append(item)
                if "@graph" in item:
                    stack.append(item["@graph"])
    return out


def _is_posting(item: dict) -> bool:
    t = item.get("@type")
    return t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t)


def _location(item: dict) -> str | None:
    locs = item.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs] if locs else []
    names = []
    for loc in locs:
        addr = (loc or {}).get("address") if isinstance(loc, dict) else None
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            parts = [p.get("name") if isinstance(p, dict) else p for p in parts]
            names.append(", ".join(str(p) for p in parts if p))
        elif isinstance(addr, str):
            names.append(addr)
    text = "; ".join(dict.fromkeys(n for n in names if n))
    if str(item.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        text = (text + " (Remote)").strip()
    return text or None


def read_posting(html: str, url: str) -> PagePosting | None:
    """The page's JobPosting, or None if it has none (or only a stub)."""
    from app.importers import html_to_text
    for item in _blocks(html):
        if not _is_posting(item):
            continue
        description = html_to_text(unescape(str(item.get("description") or "")))
        if len(description) < 200:
            continue
        org = item.get("hiringOrganization")
        company = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else None)
        return PagePosting(title=" ".join(str(item.get("title") or "").split()), company=company,
                           location=_location(item), description=description, url=url)
    return None


def fetch_posting(url: str, get_page: PageFetcher = http_get_page) -> PagePosting | None:
    try:
        return read_posting(get_page(url), url)
    except Exception:                   # unreachable, blocked, not HTML: the caller asks you to paste it
        return None


_BOARD_SITES = re.compile(r"^(linkedin|indeed|glassdoor|ziprecruiter|monster|adzuna|google|workday|greenhouse|lever|"
                          r"ashby|smartrecruiters|workable|jooble|simplyhired|talent\.com|careerbuilder)\b", re.I)


def _meta_tag(html: str, name: str) -> str | None:
    for pattern in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']+)["\']',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']'):
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return " ".join(unescape(m.group(1)).split())
    return None


def read_metadata(html: str) -> dict[str, tuple[str, str]]:
    """Title / company from the page's own metadata when it has no JobPosting block: field -> (value, source).

    * "Contoso hiring Senior SDET in Toronto, ON" (LinkedIn's og:title) -> company and title, "job page".
    * "Senior SDET at Contoso" / "Senior SDET - Contoso" page titles -> company and title, "job page".
    * og:site_name "Contoso Careers" -> company, "job page (site name)": usually right, so flagged for a check.
    Job boards' own names (LinkedIn, Indeed, ...) are never taken as the company."""
    out: dict[str, tuple[str, str]] = {}
    og_title = _meta_tag(html, "og:title")
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    page_title = " ".join(unescape(m.group(1)).split()) if m else None
    for text in (og_title, page_title):
        if not text:
            continue
        hm = re.match(r"^(.{2,80}?) hiring (.{2,120}?)(?: in (.{2,80}?))?(?: \| LinkedIn)?$", text)
        am = re.match(r"^(.{2,120}?) (?:at|@|[-–|]) (.{2,80}?)(?:\s+[-–|].*)?$", text)
        if hm and not _BOARD_SITES.match(hm.group(1)):
            out.setdefault("company", (hm.group(1).strip(), "job page"))
            out.setdefault("title", (hm.group(2).strip(), "job page"))
            if hm.group(3):
                out.setdefault("location", (hm.group(3).strip(), "job page"))
        elif am and not _BOARD_SITES.match(am.group(2)) and not re.search(r"\bjobs?\b|\bcareers?\b", am.group(1), re.I):
            out.setdefault("title", (am.group(1).strip(), "job page"))
            out.setdefault("company", (am.group(2).strip(), "job page"))
    site = _meta_tag(html, "og:site_name") or _meta_tag(html, "application-name")
    if site and not _BOARD_SITES.match(site):
        name = re.sub(r"\s*\b(careers?|jobs?|job board|recruiting|talent)\b.*$", "", site, flags=re.IGNORECASE).strip(" -|")
        if name:
            out.setdefault("company", (name, "job page (site name)"))
    return out


def fetch_page_info(url: str, get_page: PageFetcher = http_get_page) -> tuple[PagePosting | None, dict]:
    """The page's JobPosting (or None) and, when it has none, what its own metadata says (read_metadata)."""
    try:
        html = get_page(url)
    except Exception:                   # unreachable, blocked, not HTML: the caller asks you to paste it
        return None, {}
    try:
        posting = read_posting(html, url)
    except Exception:
        posting = None
    if posting is not None:
        return posting, {}
    try:
        return None, read_metadata(html)
    except Exception:
        return None, {}
