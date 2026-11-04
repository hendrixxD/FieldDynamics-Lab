"""Deploys a checked-out commit to Vercel through its REST API, using only the standard library.

It replaces the Vercel CLI, whose npm dependency tree would otherwise sit next to the token.
The token is read from VERCEL_TOKEN and only ever goes into an Authorization header.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

API_URL = "https://api.vercel.com"
PROJECT_NAME = "fielddynamics-lab"
FRAMEWORK = "fastapi"
HTTP_TIMEOUT_SECONDS = 60
BUILD_TIMEOUT_SECONDS = 600
POLL_SECONDS = 5
HEALTH_PATH = "/api/health"
HEALTH_ATTEMPTS = 3
# Vercel installs requirements.txt, and the deployed app only needs the small hashed set.
FILE_OVERRIDES = {"requirements.txt": "requirements-serve.txt"}
GLOB_CHARACTERS = frozenset("*?[!")

# (method, path, body, extra headers) -> parsed JSON response
Sender = Callable[[str, str, bytes | None, dict[str, str]], dict[str, object]]


def parse_ignore(text: str) -> list[str]:
    """Read .vercelignore entries. Only plain paths are supported, so none is misread."""
    entries = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if GLOB_CHARACTERS & set(entry):
            raise ValueError(f".vercelignore entry uses a pattern, which is not supported: {entry}")
        entries.append(entry)
    return entries


def is_ignored(path: str, entries: list[str]) -> bool:
    """Match a file against entries: a trailing slash means a folder, otherwise an exact file."""
    return any(path.startswith(e) if e.endswith("/") else path == e for e in entries)


def select_files(source: Path) -> dict[str, bytes]:
    """Return {path: content} for every tracked file Vercel should receive."""
    listing = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode()
    ignore_file = source / ".vercelignore"
    entries = parse_ignore(ignore_file.read_text()) if ignore_file.exists() else []
    files = {
        path: (source / path).read_bytes()
        for path in listing.split("\0")
        if path and not is_ignored(path, entries)
    }
    for target, replacement in FILE_OVERRIDES.items():
        files[target] = (source / replacement).read_bytes()
    return files


def deployment_body(
    project_id: str, manifest: list[dict[str, object]], production: bool
) -> dict[str, object]:
    """Build the create request. Omitting target is what makes a deployment a preview."""
    body: dict[str, object] = {
        "name": PROJECT_NAME,
        "project": project_id,
        "files": manifest,
        "projectSettings": {"framework": FRAMEWORK},
    }
    if production:
        body["target"] = "production"
    return body


def error_message(status: int, raw: bytes) -> str:
    """Describe a failed API call using Vercel's own error text where it gives one."""
    try:
        detail = json.loads(raw).get("error", {})
        reason = f"{detail.get('code', 'unknown')}: {detail.get('message', '')}"
    except (ValueError, AttributeError):
        reason = raw[:200].decode(errors="replace")
    return f"Vercel API returned {status} ({reason})"


def http_sender(token: str) -> Sender:
    """Send requests with urllib, raising Vercel's error text on failure."""

    def send(
        method: str, path: str, body: bytes | None, headers: dict[str, str]
    ) -> dict[str, object]:
        request = urllib.request.Request(
            API_URL + path,
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {token}", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(error_message(error.code, error.read())) from None
        parsed: dict[str, object] = json.loads(raw) if raw else {}
        return parsed

    return send


def upload_files(send: Sender, team_id: str, files: dict[str, bytes]) -> list[dict[str, object]]:
    """Upload each file by its SHA-1 and return the manifest the create request needs."""
    manifest: list[dict[str, object]] = []
    for path, content in sorted(files.items()):
        digest = hashlib.sha1(content).hexdigest()  # noqa: S324 - Vercel's API requires SHA-1
        headers = {
            "Content-Type": "application/octet-stream",
            "x-vercel-digest": digest,
            "Content-Length": str(len(content)),
        }
        send("POST", f"/v2/files?teamId={team_id}", content, headers)
        manifest.append({"file": path, "sha": digest, "size": len(content)})
    return manifest


def wait_until_ready(send: Sender, team_id: str, deployment_id: str) -> dict[str, object]:
    """Poll until the build ends, failing on error, cancellation, or timeout."""
    deadline = time.monotonic() + BUILD_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        deployment = send("GET", f"/v13/deployments/{deployment_id}?teamId={team_id}", None, {})
        state = deployment.get("readyState")
        if state == "READY":
            return deployment
        if state in ("ERROR", "CANCELED"):
            raise RuntimeError(f"Vercel build ended as {state}: {deployment.get('inspectorUrl')}")
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"Vercel build did not finish within {BUILD_TIMEOUT_SECONDS} seconds")


def check_health(url: str) -> None:
    """Fetch the health route, because Vercel reports READY even when the app cannot serve."""
    for attempt in range(1, HEALTH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(
                url + HEALTH_PATH, timeout=HTTP_TIMEOUT_SECONDS
            ) as response:
                if response.status == 200:
                    return
        except urllib.error.URLError:
            if attempt == HEALTH_ATTEMPTS:
                raise
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"{url}{HEALTH_PATH} did not return 200")


def deploy(send: Sender, source: Path, team_id: str, project_id: str, production: bool) -> str:
    """Upload, create, wait, and verify a deployment, returning its URL."""
    manifest = upload_files(send, team_id, select_files(source))
    body = json.dumps(deployment_body(project_id, manifest, production)).encode()
    path = f"/v13/deployments?teamId={team_id}&skipAutoDetectionConfirmation=1"
    created = send("POST", path, body, {"Content-Type": "application/json"})
    expected_target = "production" if production else None
    if created.get("target") != expected_target:
        raise RuntimeError(
            f"Vercel created a {created.get('target')} deployment, not the one asked"
        )
    deployment = wait_until_ready(send, team_id, str(created["id"]))
    url = f"https://{deployment['url']}"
    check_health(url)
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="checkout to deploy")
    parser.add_argument("--production", choices=["true", "false"], required=True)
    args = parser.parse_args()
    send = http_sender(os.environ["VERCEL_TOKEN"])
    team_id, project_id = os.environ["VERCEL_ORG_ID"], os.environ["VERCEL_PROJECT_ID"]
    url = deploy(send, args.source, team_id, project_id, args.production == "true")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"url={url}\n")
    sys.stdout.write(f"Deployed {url}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
