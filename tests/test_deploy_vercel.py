"""Checks the dependency-free Vercel deploy script without calling Vercel."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_deployer() -> ModuleType:
    """Load the script by path, because .github is not an importable package."""
    spec = importlib.util.spec_from_file_location(
        "deploy_vercel", Path(".github/scripts/deploy_vercel.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_vercel"] = module
    spec.loader.exec_module(module)
    return module


deployer = load_deployer()


def test_parse_ignore_skips_comments_and_blanks() -> None:
    assert deployer.parse_ignore("# note\n\ndocs/\nrequirements.in\n") == [
        "docs/",
        "requirements.in",
    ]


def test_parse_ignore_rejects_patterns_it_cannot_honour() -> None:
    with pytest.raises(ValueError, match="pattern"):
        deployer.parse_ignore("*.md\n")


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("docs/plan.md", True),
        ("docsy/plan.md", False),
        ("requirements.in", True),
        ("requirements.in.bak", False),
        ("api/main.py", False),
    ],
)
def test_is_ignored_matches_folders_and_exact_files(path: str, ignored: bool) -> None:
    assert deployer.is_ignored(path, ["docs/", "requirements.in"]) is ignored


def test_select_files_sends_tracked_files_and_swaps_requirements(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("doc")
    (tmp_path / "main.py").write_text("app")
    (tmp_path / "requirements.txt").write_text("full")
    (tmp_path / "requirements-serve.txt").write_text("serve")
    (tmp_path / ".vercelignore").write_text("docs/\n")
    (tmp_path / "untracked.py").write_text("x")
    git("add", "docs", "main.py", "requirements.txt", "requirements-serve.txt", ".vercelignore")

    files = deployer.select_files(tmp_path)
    assert set(files) == {".vercelignore", "main.py", "requirements.txt", "requirements-serve.txt"}
    assert files["requirements.txt"] == b"serve"


def test_preview_body_has_no_target_and_production_body_does() -> None:
    preview = deployer.deployment_body("prj_1", [], production=False)
    assert "target" not in preview
    assert preview["projectSettings"] == {"framework": "fastapi"}
    assert deployer.deployment_body("prj_1", [], production=True)["target"] == "production"


def test_error_message_uses_vercel_error_text() -> None:
    raw = json.dumps({"error": {"code": "forbidden", "message": "Not authorized"}}).encode()
    assert deployer.error_message(403, raw) == "Vercel API returned 403 (forbidden: Not authorized)"


def test_error_message_handles_a_non_json_body() -> None:
    assert deployer.error_message(502, b"Bad gateway") == "Vercel API returned 502 (Bad gateway)"


def test_upload_files_sends_sha1_and_size() -> None:
    calls: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def send(
        method: str, path: str, body: bytes | None, headers: dict[str, str]
    ) -> dict[str, object]:
        calls.append((method, path, body, headers))
        return {}

    manifest = deployer.upload_files(send, "team_1", {"main.py": b"app"})
    digest = hashlib.sha1(b"app").hexdigest()  # noqa: S324 - matches Vercel's required digest
    assert manifest == [{"file": "main.py", "sha": digest, "size": 3}]
    assert calls[0][1] == "/v2/files?teamId=team_1"
    assert calls[0][3]["x-vercel-digest"] == digest


def test_wait_until_ready_fails_on_build_error() -> None:
    def send(
        method: str, path: str, body: bytes | None, headers: dict[str, str]
    ) -> dict[str, object]:
        return {"readyState": "ERROR", "inspectorUrl": "https://vercel.test/x"}

    with pytest.raises(RuntimeError, match="ERROR"):
        deployer.wait_until_ready(send, "team_1", "dpl_1")
