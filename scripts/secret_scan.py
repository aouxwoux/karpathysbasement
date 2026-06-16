from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "pfps",
}

SKIP_SUFFIXES = {
    ".bin",
    ".db",
    ".dll",
    ".exe",
    ".gif",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyc",
    ".pyd",
    ".shm",
    ".sqlite",
    ".sqlite3",
    ".wal",
    ".zip",
}

LOCAL_SECRET_ALLOWLIST = {
    Path("bot_tokens.json"),
    Path("gemini_token.txt"),
    Path("token.txt"),
}

REQUIRED_GITIGNORE_ENTRIES = {
    "token.txt",
    "gemini_token.txt",
    "bot_tokens.json",
    ".env",
    ".env.*",
    ".bot.lock",
    "pfps/",
    ".venv/",
    "__pycache__/",
    "*.pyc",
    "*.sqlite3",
    "*.sqlite3-*",
    "*.db",
    "*.db-*",
}

SECRET_PATTERNS = {
    "discord_token": re.compile(
        r"[A-Za-z0-9_-]{23,30}\.[A-Za-z0-9_-]{6,10}\.[A-Za-z0-9_-]{25,}"
    ),
    "gemini_or_google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_pat": re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    pattern: str
    allowed: bool


def relative(path: Path) -> Path:
    return path.resolve().relative_to(ROOT)


def is_skipped(path: Path) -> bool:
    rel = relative(path)
    if any(part in SKIP_DIRS for part in rel.parts):
        return True

    return path.suffix.lower() in SKIP_SUFFIXES


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def load_gitignore_entries() -> set[str]:
    path = ROOT / ".gitignore"
    if not path.exists():
        return set()

    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.add(stripped)

    return entries


def scan_files() -> list[Finding]:
    findings: list[Finding] = []

    for path in ROOT.rglob("*"):
        if not path.is_file() or is_skipped(path):
            continue

        rel = relative(path)
        lines = read_text(path).splitlines()
        for line_number, line in enumerate(lines, start=1):
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(
                        Finding(
                            path=rel,
                            line=line_number,
                            pattern=name,
                            allowed=rel in LOCAL_SECRET_ALLOWLIST,
                        )
                    )

    return findings


def print_findings(findings: list[Finding]) -> None:
    blocking = [finding for finding in findings if not finding.allowed]
    allowed = [finding for finding in findings if finding.allowed]

    if blocking:
        print("Secret scan failed. Potential secret values were found:")
        for finding in blocking:
            print(f"- {finding.path}:{finding.line} [{finding.pattern}]")

    if allowed:
        print("Allowed local secret files containing secrets:")
        seen = sorted({(finding.path.as_posix(), finding.pattern) for finding in allowed})
        for path, pattern in seen:
            print(f"- {path} [{pattern}]")

    if not findings:
        print("No high-confidence secret patterns found.")


def main() -> int:
    gitignore_entries = load_gitignore_entries()
    missing_entries = sorted(REQUIRED_GITIGNORE_ENTRIES - gitignore_entries)
    findings = scan_files()
    blocking_findings = [finding for finding in findings if not finding.allowed]

    if missing_entries:
        print("Guardrail check failed. Missing .gitignore entries:")
        for entry in missing_entries:
            print(f"- {entry}")

    print_findings(findings)

    if missing_entries or blocking_findings:
        return 1

    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
