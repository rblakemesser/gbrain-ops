from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

RULES = {
    "private-absolute-path": re.compile(r"/Users/[^/\s]+/|/home/[^/\s]+/"),  # privacy-scan: allow
    "home-workspace-path": re.compile(r"~/(?:workspace|Desktop|Documents)/"),  # privacy-scan: allow
    "email-address": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone-number": re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)"),
    "telegram-chat-id": re.compile(r"(?<!\d)-100\d{6,}(?!\d)"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential-assignment": re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?hash)\s*[:=]\s*['\"][^${<][^'\"]{7,}"),
}
ALLOWED_EMAILS = {"user@example.com", "person@example.test"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".gz", ".zip"}

def tracked_files(root: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [root / item.decode() for item in proc.stdout.split(b"\0") if item]

def scan_repository(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in tracked_files(root):
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if "privacy-scan: allow" in line:
                continue
            for name, pattern in RULES.items():
                for match in pattern.finditer(line):
                    if name == "email-address" and (
                        match.group(0).lower() in ALLOWED_EMAILS
                        or match.group(0).lower().endswith("@users.noreply.github.com")
                    ):
                        continue
                    if name == "telegram-chat-id" and path.name.startswith("synthetic_") and "fixtures" in path.parts:
                        continue
                    findings.append(Finding(str(path.relative_to(root)), number, name))
    return findings

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default=".")
    args = parser.parse_args()
    findings = scan_repository(Path(args.path).resolve())
    for item in findings:
        print(f"{item.path}:{item.line}: {item.rule}")
    return 1 if findings else 0

if __name__ == "__main__":
    raise SystemExit(main())
