"""Scans a pull request for supply chain attacks without running any of its code.

It uses only the standard library, so it can run before anything is installed. It reads the PR
through git objects and never checks the code out, so a hostile PR has nothing to execute here.
Findings name a rule and a location but never echo PR content, which could otherwise forge
workflow commands in the log.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import PurePosixPath

HTTP_TIMEOUT_SECONDS = 30
PYPI_WORKERS = 16
OSV_URL = "https://api.osv.dev/v1/querybatch"
LOCK_FILES = ("requirements.txt", "requirements-dev.txt", "requirements-serve.txt")

# These two files must contain the patterns they look for.
SELF_PATHS = frozenset({".github/scripts/security_scan.py", "tests/test_security_scan.py"})
# These need triggers that are dangerous anywhere else, and are guarded by CODEOWNERS.
TRUSTED_WORKFLOWS = frozenset(
    {".github/workflows/security-gate.yml", ".github/workflows/deploy.yml"}
)
ALLOWED_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ico", ".parquet", ".tif", ".tiff"}
)
FORBIDDEN_SUFFIXES = frozenset(
    {".pth", ".whl", ".egg", ".so", ".pyd", ".pyc", ".dll", ".dylib", ".exe", ".bin"}
)
FORBIDDEN_NAMES = frozenset({"setup.py", ".npmrc", ".pypirc", "pip.conf", "pip.ini", "uv.toml"})
# The project has no JavaScript dependencies, and their lock files are not scanned here.
JS_MANIFESTS = frozenset(
    {
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
    }
)
BLOB_SUFFIXES = frozenset(
    {".py", ".sh", ".yml", ".yaml", ".toml", ".js", ".ts", ".html", ".cfg", ".in"}
)

CONTENT_RULES = {
    "dumps the environment": re.compile(
        r"\bprintenv\b|\benv\s*\||/proc/(self|\d+)/environ|os\.environ\.(items|copy)\(\)"
        r"|dict\(os\.environ\)|dumps\(.*os\.environ|toJSON\(secrets\)"
    ),
    "reads credential files": re.compile(
        r"\.(npmrc|pypirc|netrc|git-credentials)\b|\.ssh/|\.aws/credentials|\.config/gh/"
        r"|\.docker/config\.json|\.kube/config"
    ),
    "pipes a download into a shell": re.compile(
        r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"
    ),
    "decodes and runs hidden code": re.compile(
        r"\b(exec|eval)\s*\(\s*(base64|codecs|zlib|marshal|bytes\.fromhex|__import__)"
        r"|base64\s+(-d|--decode)\b.*\|\s*(ba)?sh\b|marshal\.loads"
    ),
    "contacts an exfiltration host": re.compile(
        r"webhook\.site|requestbin|pipedream\.net|interact\.sh|\.oast\.|ngrok(-free)?\.(io|app)"
        r"|transfer\.sh|pastebin\.com|discord(app)?\.com/api/webhooks|api\.telegram\.org/bot"
        r"|169\.254\.169\.254|metadata\.google\.internal"
    ),
    "hides text direction": re.compile("[‪-‮⁦-⁩]"),
}
BLOB_RULE = re.compile(r"[A-Za-z0-9+/=]{400,}")
UNPINNED_ACTION_RULE = re.compile(r"^\s*(-\s*)?uses:\s*(?!\./)[^@\s]+@(?![0-9a-f]{40}\b)")
RISKY_TRIGGER_RULE = re.compile(r"\b(pull_request_target|workflow_run)\b|permissions:\s*write-all")
PACKAGE_SOURCE_RULE = re.compile(
    r"--(extra-)?index-url|--find-links|--trusted-host|\bgit\+|\bhttps?://|\bfile:"
)

LOCK_LINE_RULE = re.compile(r"^\s*(#.*|--hash=sha256:[0-9a-f]{64}( \\)?)?$")
PIN_RULE = re.compile(r"^([a-z0-9][a-z0-9._-]*)(?:\[[^\]]+\])?==([^\s\\;]+)( \\)?$", re.IGNORECASE)
HASH_RULE = re.compile(r"--hash=sha256:([0-9a-f]{64})")


@dataclass(frozen=True)
class Finding:
    """One reason to stop the PR."""

    path: str
    line: int
    rule: str


def run_git(*args: str) -> bytes:
    """Run git, failing loudly, because a scan that cannot read the PR must not pass it."""
    command = ["git", "-c", "core.quotepath=off", *args]
    return subprocess.run(command, check=True, capture_output=True).stdout


def diff_args(base: str, head: str) -> list[str]:
    """Build the diff arguments, with external diff tools off so that no PR config can run."""
    return [
        "diff",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        f"{base}...{head}",
    ]


def parse_numstat(raw: bytes) -> list[tuple[str, bool]]:
    """Return (path, is_binary) for each entry of `git diff --numstat -z` output."""
    entries = []
    for record in raw.decode(errors="replace").split("\0"):
        if record:
            added, _deleted, path = record.split("\t", 2)
            entries.append((path, added == "-"))
    return entries


def parse_added_lines(diff: bytes) -> list[tuple[str, int, str]]:
    """Return (path, line number, text) for each added line of a `--unified=0` diff."""
    added: list[tuple[str, int, str]] = []
    path, line_number, remaining = "", 0, 0
    for line in diff.decode(errors="replace").split("\n"):
        # Hunk bodies come first: an added line can itself look like a "+++ b/x" header.
        if remaining > 0:
            if line.startswith("+"):
                added.append((path, line_number, line[1:]))
                line_number += 1
                remaining -= 1
            continue
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :]
        elif hunk := re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line):
            line_number = int(hunk[1])
            remaining = int(hunk[2]) if hunk[2] is not None else 1
    return added


def check_path(path: str, is_binary: bool) -> list[Finding]:
    """Flag file types that run on install or import, or that carry opaque code."""
    posix_path = PurePosixPath(path)
    name, suffix = posix_path.name, posix_path.suffix.lower()
    findings = []
    if suffix in FORBIDDEN_SUFFIXES or name in FORBIDDEN_NAMES:
        findings.append(Finding(path, 0, "adds a file type that runs on install or import"))
    if name in JS_MANIFESTS:
        findings.append(Finding(path, 0, "adds JavaScript dependencies, which no scan covers"))
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        findings.append(Finding(path, 0, "adds an env file"))
    if is_binary and suffix not in ALLOWED_BINARY_SUFFIXES | FORBIDDEN_SUFFIXES:
        findings.append(Finding(path, 0, "adds an unexpected binary file"))
    return findings


def is_workflow(path: str) -> bool:
    return path.startswith((".github/workflows/", ".github/actions/"))


def is_requirements_source(path: str) -> bool:
    return PurePosixPath(path).name.startswith("requirements") and path.endswith(".in")


def check_line(path: str, line_number: int, text: str) -> list[Finding]:
    """Apply every content rule that fits this file to one added line."""
    rules = [name for name, pattern in CONTENT_RULES.items() if pattern.search(text)]
    if PurePosixPath(path).suffix in BLOB_SUFFIXES and BLOB_RULE.search(text):
        rules.append("contains a long encoded blob")
    if is_workflow(path) and UNPINNED_ACTION_RULE.search(text):
        rules.append("uses an action not pinned to a full commit SHA")
    if is_workflow(path) and path not in TRUSTED_WORKFLOWS and RISKY_TRIGGER_RULE.search(text):
        rules.append("adds a privileged workflow trigger")
    if is_requirements_source(path) and not text.lstrip().startswith("#"):
        if PACKAGE_SOURCE_RULE.search(text):
            rules.append("installs from outside PyPI")
    return [Finding(path, line_number, rule) for rule in rules]


def scan_changes(
    entries: list[tuple[str, bool]], added: list[tuple[str, int, str]]
) -> list[Finding]:
    """Check every changed path and added line, leaving lock files to the dependency scan."""
    findings = [
        finding
        for path, is_binary in entries
        if path not in SELF_PATHS
        for finding in check_path(path, is_binary)
    ]
    for path, line_number, text in added:
        if path not in SELF_PATHS and path not in LOCK_FILES:
            findings.extend(check_line(path, line_number, text))
    return findings


def parse_lock(path: str, text: str) -> tuple[dict[tuple[str, str], set[str]], list[Finding]]:
    """Return {(name, version): hashes} and any line a generated lock file would never hold."""
    pins: dict[tuple[str, str], set[str]] = {}
    findings = []
    current: set[str] | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if pin := PIN_RULE.match(line):
            name = re.sub(r"[-_.]+", "-", pin[1].lower())
            current = pins.setdefault((name, pin[2]), set())
        elif LOCK_LINE_RULE.match(line):
            if (digest := HASH_RULE.search(line)) and current is not None:
                current.add(digest[1])
        else:
            findings.append(Finding(path, line_number, "lock file has an unexpected line"))
    findings.extend(
        Finding(path, 0, f"pin has no hash: {name}=={version}")
        for (name, version), hashes in pins.items()
        if not hashes
    )
    return pins, findings


def classify_advisories(name: str, version: str, advisory_ids: Iterable[str]) -> list[Finding]:
    """Turn OSV advisory ids into findings, calling out packages reported as malware."""
    findings = []
    for advisory_id in advisory_ids:
        label = (
            "known malicious package" if advisory_id.startswith("MAL-") else "known vulnerability"
        )
        findings.append(Finding("requirements", 0, f"{label}: {name}=={version} ({advisory_id})"))
    return findings


def check_release(
    name: str,
    version: str,
    hashes: set[str],
    release: dict[str, object],
) -> list[Finding]:
    """Check a newly added pin against its PyPI record: hashes and yanked status."""
    label = f"{name}=={version}"
    files = release.get("urls")
    if not isinstance(files, list) or not files:
        return [Finding("requirements", 0, f"pin has no files on PyPI: {label}")]
    published = {entry["digests"]["sha256"]: entry for entry in files}
    findings = []
    if unknown := hashes - published.keys():
        findings.append(Finding("requirements", 0, f"hash not on PyPI: {label} ({len(unknown)})"))
    pinned_files = [published[digest] for digest in hashes & published.keys()]
    if any(entry["yanked"] for entry in pinned_files):
        findings.append(Finding("requirements", 0, f"yanked release: {label}"))
    return findings


def fetch_json(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    """GET or POST JSON. Errors propagate, so an unreachable source fails the scan closed."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        result: dict[str, object] = json.load(response)
    return result


def query_osv(pins: list[tuple[str, str]]) -> list[Finding]:
    """Ask OSV about every pin, because advisories appear after a version was first accepted."""
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in pins]
    results = fetch_json(OSV_URL, {"queries": queries})["results"]
    if not isinstance(results, list):
        raise ValueError("OSV returned an unexpected body")
    findings = []
    for (name, version), result in zip(pins, results, strict=True):
        advisories = [vuln["id"] for vuln in result.get("vulns", [])]
        findings.extend(classify_advisories(name, version, advisories))
    return findings


def query_pypi(name: str, version: str, hashes: set[str]) -> list[Finding]:
    """Check one new pin on PyPI, reporting a missing release as a finding."""
    try:
        release = fetch_json(f"https://pypi.org/pypi/{name}/{version}/json")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return [Finding("requirements", 0, f"not on PyPI: {name}=={version}")]
        raise
    return check_release(name, version, hashes, release)


def read_at(revision: str, path: str) -> str:
    """Read a file from a commit, treating a file absent from it as empty."""
    try:
        return run_git("show", f"{revision}:{path}").decode(errors="replace")
    except subprocess.CalledProcessError:
        return ""


def scan_dependencies(base: str, head: str) -> list[Finding]:
    """Check all pins against OSV, and pins new to this PR against PyPI."""
    findings = []
    head_pins: dict[tuple[str, str], set[str]] = {}
    known: dict[tuple[str, str], set[str]] = {}
    for path in LOCK_FILES:
        pins, lock_findings = parse_lock(path, read_at(head, path))
        findings.extend(lock_findings)
        for pin, hashes in pins.items():
            head_pins.setdefault(pin, set()).update(hashes)
        for pin, hashes in parse_lock(path, read_at(base, path))[0].items():
            known.setdefault(pin, set()).update(hashes)
    if not head_pins:
        return findings + [Finding("requirements", 0, "no pins found in any lock file")]
    findings.extend(query_osv(sorted(head_pins)))
    new_pins = [(pin, h) for pin, h in head_pins.items() if not h <= known.get(pin, set())]
    with ThreadPoolExecutor(PYPI_WORKERS) as pool:
        checks = [
            pool.submit(query_pypi, name, version, hashes) for (name, version), hashes in new_pins
        ]
    for check in checks:
        findings.extend(check.result())
    return findings


def scan(base: str, head: str) -> list[Finding]:
    """Run every check on the change from base to head."""
    # Deleted files are left out: removing a risky file is never a reason to stop a PR.
    entries = parse_numstat(run_git(*diff_args(base, head), "--numstat", "-z", "--diff-filter=d"))
    added = parse_added_lines(run_git(*diff_args(base, head), "--unified=0"))
    return scan_changes(entries, added) + scan_dependencies(base, head)


def report(findings: list[Finding]) -> None:
    """Write findings as error annotations, keeping paths to plain characters."""
    for finding in findings:
        path = re.sub(r"[^\w./@+-]", "_", finding.path)
        rule = re.sub(r"[^\w .=()-]", "_", finding.rule)
        sys.stdout.write(f"::error title=Security scan::{path}:{finding.line} {rule}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="commit the change is measured from")
    parser.add_argument("--head", required=True, help="commit under review")
    args = parser.parse_args()
    findings = scan(args.base, args.head)
    report(findings)
    sys.stdout.write(f"Security scan finished with {len(findings)} finding(s).\n")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
