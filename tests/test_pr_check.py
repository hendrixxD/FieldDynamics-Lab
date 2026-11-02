"""Checks the branch naming and template rules enforced on every PR."""

import os
import subprocess

import pytest

SCRIPT_PATH = ".github/scripts/check-pr.sh"
FEATURE_BODY = "- [x] New feature\n- [ ] Fix\n- [X] Low: docs\n- [ ] High: scoring\n"
FIX_BODY = "- [x] Fix\r\n- [ ] New feature\r\n- [x] High: scoring\r\n"


def run_check(head: str, base: str, body: str) -> subprocess.CompletedProcess[str]:
    """Run the script as the workflow does, with PR details in the environment."""
    env = {**os.environ, "PR_HEAD": head, "PR_BASE": base, "PR_BODY": body}
    return subprocess.run(["bash", SCRIPT_PATH], env=env, capture_output=True, text=True)


@pytest.mark.parametrize(
    ("head", "base", "body"),
    [
        ("feat-rain_onset", "staging", FEATURE_BODY),
        ("feat-rain-onset", "staging", FEATURE_BODY),
        ("fix-lga_join", "staging", FIX_BODY),
        ("staging", "main", ""),
        ("main", "staging", ""),
    ],
)
def test_valid_pr_passes(head: str, base: str, body: str) -> None:
    result = run_check(head, base, body)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    ("head", "base", "body", "message"),
    [
        ("rain_onset", "staging", FEATURE_BODY, "must be named"),
        ("feat/rain_onset", "staging", FEATURE_BODY, "must be named"),
        ("feat-", "staging", FEATURE_BODY, "must be named"),
        ("feat-Rain", "staging", FEATURE_BODY, "must be named"),
        ("feat-rain_onset", "main", FEATURE_BODY, "must target staging"),
        ("staging", "feat-rain_onset", "", "must target main"),
        ("feat-rain_onset", "staging", "", "exactly one type"),
        ("feat-rain_onset", "staging", "- [x] Fix\n- [x] Low: docs\n", "must tick"),
        ("fix-lga_join", "staging", FEATURE_BODY, "must tick"),
        ("feat-rain_onset", "staging", "- [x] New feature\n", "exactly one level"),
        ("feat-rain_onset", "staging", "- [x] New feature\n- [x] Fix\n- [x] Low\n", "one type"),
        (
            "feat-rain_onset",
            "staging",
            "- [x] New feature\n- [x] Low: a\n- [x] High: b\n",
            "exactly one level",
        ),
    ],
)
def test_invalid_pr_fails(head: str, base: str, body: str, message: str) -> None:
    result = run_check(head, base, body)
    assert result.returncode == 1
    assert message in result.stdout


def test_branch_name_cannot_run_as_code() -> None:
    result = run_check('feat-$(echo "ran" >&2)', "staging", FEATURE_BODY)
    assert result.returncode == 1
    assert "ran" not in result.stderr
