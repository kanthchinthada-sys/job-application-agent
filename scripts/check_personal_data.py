"""Check that no personal data is about to be published.

    python scripts/check_personal_data.py            # checks every file git would publish

It looks for:
* real-looking email addresses and phone numbers (example.com / 555 numbers are fine),
* any word or phrase listed in private/personal_terms.txt (one per line: your name, employers, email, city...).
  That file is git-ignored, so your list itself is never published.

Exit code 1 (and a list of file:line hits) if anything is found, so it can guard a commit or a push.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMS_FILE = ROOT / "private" / "personal_terms.txt"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<![\w.])\+?1?[\s(.-]*\d{3}[\s).-]+\d{3}[\s.-]+\d{4}(?![\w])")
ALLOWED_EMAIL = re.compile(r"@(example\.(com|org|net)|users\.noreply\.github\.com|anthropic\.com)$|^noreply@", re.IGNORECASE)
SKIP = re.compile(r"(^|/)(package-lock\.json|.*\.(png|jpg|jpeg|gif|ico|pdf|woff2?|ttf|lock))$")


def published_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return [ROOT / f for f in out.splitlines() if f and not SKIP.search(f)]


def main() -> int:
    terms = []
    if TERMS_FILE.exists():
        terms = [t.strip() for t in TERMS_FILE.read_text(encoding="utf-8").splitlines()
                 if t.strip() and not t.startswith("#")]
    hits = []
    for path in published_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        rel = path.relative_to(ROOT)
        for n, line in enumerate(lines, 1):
            for m in EMAIL.finditer(line):
                if not ALLOWED_EMAIL.search(m.group(0)):
                    hits.append(f"{rel}:{n}: email address {m.group(0)}")
            for m in PHONE.finditer(line):
                digits = re.sub(r"\D", "", m.group(0))
                if "555" not in digits[-10:-4] and not re.fullmatch(r"(\d)\1+", digits[-7:]):
                    hits.append(f"{rel}:{n}: phone-like number {m.group(0).strip()}")
            low = line.lower()
            for t in terms:
                if re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", low):
                    hits.append(f"{rel}:{n}: personal term '{t}'")
    if not TERMS_FILE.exists():
        print(f"Note: {TERMS_FILE.relative_to(ROOT)} not found; only emails and phone numbers were checked.")
    if hits:
        print("Personal data found; remove it before publishing:")
        print("\n".join(hits))
        return 1
    print(f"OK: no personal data found in {len(published_files())} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
