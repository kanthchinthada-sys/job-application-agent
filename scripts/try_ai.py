"""Try the AI skill extraction against your real local Ollama.

Run from the project folder:   python scripts/try_ai.py
Uses the FICTIONAL job in examples/fictional_job.json. Nothing is saved.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extraction import extract_skills  # noqa: E402
from app.llm import LLMUnavailableError, OllamaClient  # noqa: E402

llm = OllamaClient.from_env()
print("Ollama status:", llm.status())

job = json.loads((Path(__file__).resolve().parent.parent / "examples" / "fictional_job.json").read_text())
print(f"\nExtracting skills from: {job['title']} (model {llm.model}) ...")
start = time.perf_counter()
try:
    result = extract_skills(llm, job["description"])
except LLMUnavailableError as exc:
    sys.exit(f"\nERROR: {exc}")
print(f"Done in {time.perf_counter() - start:.1f}s\n")
print(json.dumps(result.model_dump(), indent=2))
print("\nSkills you typed by hand for this job:", job["required_skills"])
