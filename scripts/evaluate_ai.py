"""Measure the local AI's quality on FICTIONAL cases. Needs Ollama running.

  python scripts/evaluate_ai.py                    full run: quality, repeated runs, rewording, injection attacks
  python scripts/evaluate_ai.py --save-baseline    also save this run as the baseline (data/eval/baseline.json)
  python scripts/evaluate_ai.py --compare          compare with the baseline; exit code 1 if anything got worse
  python scripts/evaluate_ai.py --repeat 5 --quick (--quick: skip cover letters and robustness checks)

Use --compare after changing a prompt or the model (OLLAMA_MODEL=... to try another one). Reports are saved to
data/eval/ (git-ignored).
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_ROOT  # noqa: E402
from app.evaluation import compare, run_all  # noqa: E402
from app.llm import LLMUnavailableError, OllamaClient  # noqa: E402

BASELINE = PROJECT_ROOT / "data" / "eval" / "baseline.json"

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--repeat", type=int, default=3, help="extraction runs per case for the variation check")
parser.add_argument("--quick", action="store_true", help="extraction only: no letters, no robustness checks")
parser.add_argument("--save-baseline", action="store_true", help="save this run as the baseline")
parser.add_argument("--compare", action="store_true", help="compare with the saved baseline")
args = parser.parse_args()

llm = OllamaClient.from_env()
print(f"Evaluating {llm.model} on fictional cases (several minutes)...")
try:
    report = run_all(llm, repeat=args.repeat, letters=not args.quick, robustness=not args.quick)
except LLMUnavailableError as exc:
    sys.exit(f"ERROR: {exc}")
out = PROJECT_ROOT / "data" / "eval" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2))
print(json.dumps(report["summary"], indent=2))
for r in report["results"]:
    print(f"\n{r['case']}: extraction recall {r['extraction']['recall']}, precision {r['extraction']['precision']}, "
          f"missed {r['extraction']['missed']}")
    if "repeated_runs" in r:
        rr = r["repeated_runs"]
        print(f"  {rr['runs']} runs: stability {rr['stability']}, systematic misses {rr['systematic_misses']}, "
              f"random misses {rr['random_misses']}")
        print(f"  reworded postings: consistency {r['metamorphic']['average_consistency']}")
    if "cover_letter" in r:
        c = r["cover_letter"]
        print(f"  letter: {c['honesty_warnings']} warnings, humanizer {c['humanizer_score']}, {c['words']} words")
if "adversarial" in report:
    a = report["adversarial"]
    print(f"\nInjection attacks: {a['letters_with_leaks']} of {a['cases']} letters repeated an injected claim, "
          f"{a['unflagged_leaks']} of those claims not flagged")
print(f"\nFull report: {out}")

if args.save_baseline:
    BASELINE.write_text(json.dumps(report["summary"], indent=2))
    print(f"Saved as baseline: {BASELINE}")
if args.compare:
    if not BASELINE.exists():
        sys.exit("No baseline yet. Run once with --save-baseline.")
    base = json.loads(BASELINE.read_text())
    print(f"\nCompared with baseline (prompts {base.get('prompt_version')}, model {base.get('model')}) -> "
          f"now (prompts {report['summary']['prompt_version']}, model {report['summary']['model']}):")
    problems = compare(base, report["summary"])
    print("\n".join(f"  WORSE  {p}" for p in problems) or "  no regressions")
    sys.exit(1 if problems else 0)
