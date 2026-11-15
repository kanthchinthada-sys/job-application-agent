"""Measured results for this project: real numbers, collected from the code, the tests and your own usage.

Run from the project folder:   python scripts/metrics.py
Writes data/results.md (git-ignored, because the usage section comes from your own database) and prints it.
Nothing is estimated: every number below is counted or measured when you run this.
"""

import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run_tests(marker: str) -> tuple[int, int, float]:
    """Run one test group; return (passed, failed, seconds)."""
    start = time.perf_counter()
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", marker],
                         cwd=ROOT, capture_output=True, text=True).stdout
    secs = time.perf_counter() - start
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    return passed, failed, secs


def count_lines(folder: str) -> int:
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in (ROOT / folder).rglob("*.py"))


def endpoints() -> int:
    import tempfile
    from app.api import create_app
    with tempfile.TemporaryDirectory() as d:
        app = create_app(Path(d) / "m.db")
        return sum(len(ops) for ops in app.openapi()["paths"].values())   # documented operations


def latest_eval() -> dict | None:
    reports = sorted((ROOT / "data" / "eval").glob("*.json"))
    return json.loads(reports[-1].read_text()) if reports else None


def usage() -> dict | None:
    db = ROOT / "data" / "job_agent.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(db)
    try:
        q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731
        return {
            "jobs": q("SELECT COUNT(*) FROM jobs")[0][0],
            "applications": dict(q("SELECT status, COUNT(*) FROM applications GROUP BY status")),
            "documents": dict(q("SELECT kind, COUNT(*) FROM documents GROUP BY kind")),
        }
    finally:
        conn.close()


def main(with_e2e: bool) -> None:
    print("Running the tests (this takes a minute or two)...")
    unit = run_tests("not e2e")
    e2e = run_tests("e2e") if with_e2e else None
    lines = [f"# Measured results ({datetime.now():%Y-%m-%d})", "",
             "## Engineering", "",
             f"- Automated API tests: **{unit[0]} passed**, {unit[1]} failed, in {unit[2]:.1f} s"]
    if e2e:
        lines.append(f"- End-to-end browser tests (Playwright): **{e2e[0]} passed**, {e2e[1]} failed, in {e2e[2]:.1f} s")
    lines += [f"- API endpoints: {endpoints()}",
              f"- Code: {count_lines('app')} lines of Python in app/, {count_lines('tests')} lines of tests",
              "- Cost to run: $0 (local Ollama model, SQLite, no cloud services)", ""]

    ev = latest_eval()
    lines += ["## AI quality (fictional evaluation cases, scripts/evaluate_ai.py)", ""]
    if ev:
        s = ev["summary"]
        lines += [f"- Model: {s['model']}, {s['cases']} cases, run {ev['run_at']}",
                  f"- Skill extraction: recall {s['extraction_recall']}, precision {s['extraction_precision']}",
                  f"- Invented skills caught and dropped: {s['hallucinated_skills_dropped']}"]
        if "letters_with_zero_warnings" in s:
            lines += [f"- Cover letters with zero honesty warnings: {s['letters_with_zero_warnings']} of {s['cases']}",
                      f"- Average humanizer score: {s['avg_humanizer_score']} / 100",
                      f"- Letters with correct opening and signature: {s['letters_structured_correctly']} of {s['cases']}"]
    else:
        lines.append("- No evaluation yet. Run: python scripts/evaluate_ai.py (needs Ollama)")
    lines.append("")

    u = usage()
    lines += ["## Your usage (from data/job_agent.db)", ""]
    if u:
        lines += [f"- Jobs saved: {u['jobs']}",
                  f"- Applications by status: {u['applications'] or 'none yet'}",
                  f"- Documents generated: {u['documents'] or 'none yet'}"]
    else:
        lines.append("- No database yet.")

    text = "\n".join(lines) + "\n"
    out = ROOT / "data" / "results.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("\n" + text + f"\nSaved to {out}")


if __name__ == "__main__":
    main(with_e2e="--e2e" in sys.argv)
