"""Humanizer: detect and remove common signs of AI-written prose.

Based on the patterns catalogued in Wikipedia's "Signs of AI writing" guide
(WikiProject AI Cleanup), adapted for cover letters and application answers.

``find_ai_patterns`` is deterministic (regex only), so it is fast, free and
unit-testable. ``score`` turns the findings into a 0-100 number: 100 means no
known AI tells were found. It is a heuristic, not an AI detector.
"""

import re

from pydantic import BaseModel

# Rules the model follows while writing (also used for the rewrite pass).
STYLE_RULES = """Write like a real person who is good at their job, not like an AI:
- Plain, specific words. Say "use" not "leverage", "help" not "foster", "important" sparingly.
- Mix short and longer sentences. Contractions are fine (I've, I'm, didn't).
- No em dashes. Use commas, periods or parentheses.
- No lists of three adjectives or three parallel phrases just for rhythm.
- No grand openings or closings: never "I am writing to express my interest", "I am thrilled/excited",
  "I look forward to the opportunity", "thank you for considering my application".
- No hype: never "passionate", "proven track record", "perfect fit", "unique opportunity",
  "fast-paced", "cutting-edge", "dynamic", "seamless", "robust", "meticulous", "invaluable".
- No AI-favourite words: delve, testament, tapestry, landscape, journey, pivotal, crucial, showcase,
  underscore, foster, leverage, spearhead, synergy, ever-evolving, realm, navigate (figuratively).
- No "not only X but also Y", no "Furthermore/Moreover/Additionally" to start sentences.
- Don't explain why something matters in vague terms ("ensuring high quality"); give the concrete detail.
- Start with something specific about the work, not about your feelings."""

# (name, regex, penalty). Patterns are matched case-insensitively on the draft.
_PATTERNS: list[tuple[str, str, int]] = [
    ("AI-favourite word", r"\b(delv(e|es|ing)|testament|tapestry|landscape|pivotal|crucial|showcas(e|es|ing)|"
     r"underscor(e|es|ing)|foster(s|ing)?|leverag(e|es|ing)|spearhead(ed|ing)?|synerg(y|ies)|realm|"
     r"ever-evolving|meticulous(ly)?|invaluable|seamless(ly)?|robust|cutting-edge|dynamic|holistic|"
     r"multifaceted|navigat(e|ing) the)\b", 5),
    ("hype phrase", r"\b(passionate|proven track record|perfect fit|unique opportunity|fast-paced|"
     r"deep understanding|commitment to excellence|strong foundation|wealth of experience|"
     r"make a (meaningful|significant) impact|hit the ground running|go above and beyond)\b", 6),
    ("formulaic opening", r"\b(I am writing to (express|apply)|I('m| am) (thrilled|excited|delighted|eager)|"
     r"It is with great)\b", 8),
    ("formulaic closing", r"\b(I look forward to (the opportunity|hearing|discussing)|"
     r"thank you for (your time and )?consider(ing|ation)|I would welcome the opportunity|"
     r"I am confident (that )?(my|I))\b", 6),
    ("em dash", r"—|\s--\s", 3),
    ("not only... but also", r"\bnot only\b[^.]{0,80}\bbut also\b", 6),
    ("stiff transition", r"(^|[.!?]\s+)(Furthermore|Moreover|Additionally|In addition|Ultimately|Notably),", 4),
    ("vague significance", r"\b(ensuring (high|the highest) quality|drive (innovation|success|results)|"
     r"in today's|plays? a (key|vital|critical) role|align(s)? (perfectly|closely|well) with)\b", 5),
    ("rule of three", r"\b(\w+), (\w+),? and (\w+) (solutions|results|outcomes|teams|environments)\b", 4),
]
_COMPILED = [(name, re.compile(rx, re.IGNORECASE | re.MULTILINE), pen) for name, rx, pen in _PATTERNS]


class Finding(BaseModel):
    pattern: str
    text: str
    penalty: int


class HumanizerReport(BaseModel):
    score: int            # 100 = no known AI tells found
    findings: list[Finding]


def find_ai_patterns(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for name, rx, penalty in _COMPILED:
        for m in rx.finditer(text):
            findings.append(Finding(pattern=name, text=m.group(0).strip(" .,"), penalty=penalty))
    # Uniform sentence length is a classic tell: flag when most sentences are 18-30 words.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) > 3]
    if len(sentences) >= 6:
        mid = sum(1 for s in sentences if 18 <= len(s.split()) <= 30)
        if mid / len(sentences) > 0.7:
            findings.append(Finding(pattern="monotone sentence length", text=f"{mid} of {len(sentences)} sentences are 18-30 words", penalty=5))
    return findings


def report(text: str) -> HumanizerReport:
    findings = find_ai_patterns(text)
    return HumanizerReport(score=max(0, 100 - sum(f.penalty for f in findings)), findings=findings)
