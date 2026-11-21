"""Time the normal "prepare" workflow (tailored resume + cover letter) for some saved jobs.

    python scripts/benchmark_prepare.py --jobs 3 8 --runs 2              # your real model (Ollama)
    python scripts/benchmark_prepare.py --jobs 3 8 --runs 2 --legacy     # the old, one-after-the-other flow
    python scripts/benchmark_prepare.py --jobs 3 8 --fake                # no model: code time and prompt sizes only

It works on a COPY of data/job_agent.db (your database is never changed) and prints, per job and run: total time
until both documents are ready, time per stage, model calls, tokens (as Ollama reports them) and retries.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, database, metrics, preparation, repository  # noqa: E402
from app.llm import OllamaClient  # noqa: E402


class FakeModel:
    """Answers instantly with valid, empty-ish JSON: measures everything except the model itself."""
    model = "fake"

    def generate_json(self, system, user, schema):
        props = schema.get("properties", {})
        if "text" in props:     # cover letter
            return {"text": "I build reliable test automation. " * 40, "fact_ids_used": [], "questions_for_candidate": []}
        return {k: [] for k in props}


def run_once(db: Path, llm, job_id: int, legacy: bool) -> dict:
    conn = database.connect(db)
    row = conn.execute("SELECT id FROM applications WHERE job_id = ? ORDER BY id LIMIT 1", (job_id,)).fetchone()
    from app.schemas import ApplicationCreate
    app_id = row["id"] if row else repository.create_application(conn, ApplicationCreate(job_id=job_id)).id
    conn.close()
    prep = preparation.Preparer(db, llm, legacy=legacy)
    start = time.perf_counter()
    prep.start(app_id)
    prep.wait(app_id, timeout=1800)
    total = time.perf_counter() - start
    status = prep.get(app_id)
    report = dict(status.timing or {})
    report["total_seconds"] = round(total, 2)
    report["state"] = status.state
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, nargs="+", required=True, help="saved job ids (see the Jobs page)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--legacy", action="store_true", help="the old flow: resume, then letter, each analysing the job")
    ap.add_argument("--fake", action="store_true", help="no model: measure the code only")
    ap.add_argument("--db", type=Path, help="database to copy (default: your app's database)")
    args = ap.parse_args()
    config.load_env_file()
    llm = FakeModel() if args.fake else OllamaClient.from_env()
    src = args.db or config.get_db_path()
    for job_id in args.jobs:
        totals = []
        for n in range(args.runs):
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "copy.db"
                shutil.copy(src, db)
                r = run_once(db, llm, job_id, args.legacy)
            totals.append(r["total_seconds"])
            stages = sorted(r.get("stages", {}).items(), key=lambda kv: -kv[1])
            print(f"\njob {job_id} run {n + 1} ({'legacy' if args.legacy else 'current'}): {r['state']}, "
                  f"total {r['total_seconds']}s, model calls {r.get('model_calls')}, model time {r.get('model_seconds')}s, "
                  f"tokens in/out {r.get('input_tokens')}/{r.get('output_tokens')}, retries {r.get('retries')}, "
                  f"prompt chars {r.get('prompt_chars')}")
            for name, sec in stages[:8]:
                print(f"   {sec:8.2f}s  {name}")
            for c in r.get("calls", []):
                print(f"   call: {c['purpose']:<28} {c['seconds']:7.2f}s  prompt {c['prompt_chars']} chars"
                      f"  tokens {c.get('input_tokens')}/{c.get('output_tokens')}  load {c.get('load_seconds')}"
                      f"  waited {c.get('queue_seconds')}s")
        if len(totals) > 1:
            print(f"job {job_id}: median total {statistics.median(totals):.2f}s over {len(totals)} runs")


if __name__ == "__main__":
    main()
