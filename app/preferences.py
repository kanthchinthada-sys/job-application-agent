"""Check a posting's title and location against your job preferences.

Deterministic and explainable: every result says *why* it matched or not.
"""

import re

from pydantic import BaseModel

from app.schemas import JobPreferences

CANADA_PLACES = [
    "toronto", "mississauga", "brampton", "oakville", "waterloo", "kitchener", "cambridge",
    "guelph", "hamilton", "markham", "vaughan", "ottawa", "london, on", "quebec", "québec", "montreal", "montréal",
    "british columbia", "vancouver", "burnaby", "victoria, bc", "alberta", "calgary", "edmonton", "manitoba",
    "winnipeg", "saskatchewan", "regina", "saskatoon", "nova scotia", "halifax", "new brunswick", "fredericton",
    "moncton", "saint john", "newfoundland", "prince edward island",
    "ontario", "canada",   # last, so a specific city is named in the reason when there is one
]

# Different ways postings name the same role, normalised before comparing.
_SYNONYMS = [
    (r"\bquality assurance\b", "qa"),
    (r"\bsoftware (development )?engineer in test\b", "sdet"),
    (r"\b(sr\.?|snr)\b", "senior"),
    (r"\bqe\b", "quality engineer"),
    (r"\btest automation\b", "automation"),
    (r"\bautomated test(ing)?\b", "automation"),
    (r"\bfde\b", "forward deployed engineer"),
]
_GENERIC = {"senior", "the", "and", "of", "i", "ii", "iii", "-", "&"}


class PreferenceCheck(BaseModel):
    title_match: str        # "strong", "partial" or "none"
    title_reason: str
    location_match: str     # "yes", "unclear" or "no"
    location_reason: str

    @property
    def suitable(self) -> bool:
        return self.title_match != "none" and self.location_match != "no"


def _tokens(text: str) -> set[str]:
    t = text.lower()
    for pattern, repl in _SYNONYMS:
        t = re.sub(pattern, repl, t)
    return {w for w in re.findall(r"[a-z0-9+#]+", t) if w not in _GENERIC}


def check_title(title: str, target_roles: list[str]) -> tuple[str, str]:
    if not target_roles:
        return "strong", "no target roles set"
    title_tokens = _tokens(title)
    best_role, best = None, 0.0
    for role in target_roles:
        role_tokens = _tokens(role)
        if not role_tokens:
            continue
        score = len(role_tokens & title_tokens) / len(role_tokens)
        if score > best:
            best_role, best = role, score
    if best == 1:
        return "strong", f"matches your target role '{best_role}'"
    if best >= 0.6:
        return "partial", f"close to your target role '{best_role}'"
    return "none", "doesn't match your target roles"


def check_location(location: str | None, preferred: list[str]) -> tuple[str, str]:
    if not preferred:
        return "yes", "no location preference set"
    loc = (location or "").lower()
    if not loc:
        return "unclear", "the posting doesn't state a location"
    for pref in preferred:
        p = pref.lower().strip()
        if p == "canada":
            hit = next((place for place in CANADA_PLACES if place in loc), None)
            if hit:
                return "yes", f"in Canada ({hit})"
        elif p and p in loc:
            return "yes", f"matches '{pref}'"
    if "remote" in loc and not re.search(r"remote\s*\(?\s*(us|usa|united states|uk|europe|emea|apac)", loc):
        return "unclear", "remote, but the country isn't stated; check the posting"
    return "no", f"not in {', '.join(preferred)}"


def check(title: str, location: str | None, prefs: JobPreferences) -> PreferenceCheck:
    tm, tr = check_title(title, prefs.target_roles)
    lm, lr = check_location(location, prefs.locations)
    return PreferenceCheck(title_match=tm, title_reason=tr, location_match=lm, location_reason=lr)
