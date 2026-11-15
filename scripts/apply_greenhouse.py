"""Assisted apply on Greenhouse: fills the real form in a visible browser. YOU click Submit.

Before running:
  1. The app is running (uvicorn app.main:app) and you approved the plan in Applications -> Materials.
  2. pip install playwright==1.63.0 && python -m playwright install chromium   (one time)

Run from the project folder:
  python scripts/apply_greenhouse.py --application 1

What it does:
  * checks the application isn't already submitted (no duplicates),
  * saves your latest tailored resume (and cover letter, if any) as PDFs in data/submissions/,
  * opens the job's form in a visible browser and fills your approved answers,
  * STOPS. You review everything and click "Submit application" yourself,
  * watches what happens and records it: confirmed (confirmation page seen), uncertain, or not submitted.
"""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_ROOT  # noqa: E402


def api(method: str, base: str, path: str, **kw):
    r = httpx.request(method, base + path, timeout=30, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        sys.exit(f"\nStopped: {detail}")
    return r.json() if "json" in r.headers.get("content-type", "") else r.text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--application", type=int, required=True, help="application id (see the Applications tab)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--wait-minutes", type=float, default=30, help="how long to wait for you to submit")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
        from app.greenhouse_filler import fill_form, watch_for_submission
    except ImportError:
        sys.exit("Playwright isn't installed: pip install playwright==1.63.0 && python -m playwright install chromium")

    base = args.base_url.rstrip("/")
    ready = api("GET", base, f"/applications/{args.application}/submission/ready")
    plan = ready["plan"]
    print(f"\nApplication {args.application}: {ready['job_title']} at {ready['company']}")
    if not ready["has_resume"]:
        sys.exit("Stopped: no tailored resume yet. Click 'Tailor resume' in Materials first.")

    out_dir = PROJECT_ROOT / "data" / "submissions" / f"application-{args.application}"
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # 1) PDFs from your latest materials (headless, same look as the printable pages)
        headless = p.chromium.launch()
        files: dict[str, Path] = {}
        pdf_page = headless.new_page()
        pdf_page.goto(f"{base}/applications/{args.application}/resume")
        first = next((f["answer"] for f in plan["fields"] if f["name"] == "first_name"), "")
        last = next((f["answer"] for f in plan["fields"] if f["name"] == "last_name"), "")
        stem = "_".join(x for x in [first, last] if x).replace(" ", "_") or "My"
        files["resume"] = out_dir / f"{stem}_Resume.pdf"
        pdf_page.pdf(path=str(files["resume"]), format="Letter", print_background=True)
        if ready["has_cover_letter"]:
            pdf_page.goto(f"{base}/applications/{args.application}/cover-letter")
            files["cover_letter"] = files["resume"].with_name(files["resume"].name.replace("Resume", "Cover_Letter"))
            pdf_page.pdf(path=str(files["cover_letter"]), format="Letter", print_background=True)
        headless.close()
        print("Saved: " + ", ".join(str(f.relative_to(PROJECT_ROOT)) for f in files.values()))

        # 2) Fill the real form in a visible browser
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        print(f"Opening {plan['apply_url']} and filling your approved answers...")
        report = fill_form(page, plan, files)
        print(f"  filled: {len(report.filled)}   left blank on purpose: {len(report.skipped)}   "
              f"not on this form: {len(report.not_found)}")
        for problem in report.problems:
            print(f"  PROBLEM: {problem}")
        api("POST", base, f"/applications/{args.application}/submission/result",
            json={"outcome": "filled" if report.ok else "failed",
                  "details": f"{len(report.filled)} fields filled" + (f"; {'; '.join(report.problems)}" if report.problems else "")})

        print("\n>>> Review EVERYTHING in the browser window, fix anything that's wrong, answer the questions left "
              "blank,\n>>> then click 'Submit application' yourself. I won't submit for you.")
        print(f">>> Waiting up to {args.wait_minutes:.0f} minutes. Close the browser to stop without submitting.\n")
        try:
            outcome = watch_for_submission(page, timeout_s=args.wait_minutes * 60)
        except KeyboardInterrupt:
            from app.greenhouse_filler import Outcome
            outcome = Outcome("not_submitted", details="Stopped with Ctrl+C.")

        result = api("POST", base, f"/applications/{args.application}/submission/result",
                     json={"outcome": outcome.outcome, "evidence": outcome.evidence, "details": outcome.details})
        print(f"Result: {outcome.outcome}. {outcome.evidence or outcome.details}")
        print(f"Application status is now: {result['status']}")
        if outcome.outcome == "uncertain":
            print("Check your email / the company's portal. Update the status in the app when you know.")
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
