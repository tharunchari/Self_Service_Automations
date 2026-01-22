import os
import requests
import json
from datetime import datetime, timezone
from pathlib import Path

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 2))

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

INFRA_FAILURE_PATTERNS = [
    "runner unexpectedly disconnected",
    "received a shutdown signal",
    "lost communication with the server",
    "the operation was canceled"
]

APP_FAILURE_PATTERNS = [
    "test failed",
    "lint",
    "compilation error"
]

# Local state directory in repo
STATE_DIR = Path(".github/ci-retry-state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Get repo from GITHUB_REPOSITORY env
REPO_FULL = os.environ.get("GITHUB_REPOSITORY")  # e.g., 'vitechsystems/myrepo'
ORG, REPO = REPO_FULL.split("/")

def main():
    print(f"Dry-Run Auto-Retry for {REPO_FULL}")
    runs = get_failed_pr_runs()
    for run in runs:
        classify_run(run)

def get_failed_pr_runs():
    url = f"https://api.github.com/repos/{ORG}/{REPO}/actions/runs?event=pull_request&status=failure&per_page=20"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()["workflow_runs"]

def get_run_logs(run_id):
    url = f"https://api.github.com/repos/{ORG}/{REPO}/actions/runs/{run_id}/logs"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        return ""
    return resp.text.lower()

def classify_run(run):
    run_id = str(run["id"])
    logs = get_run_logs(run_id)
    infra_failure = any(pat in logs for pat in INFRA_FAILURE_PATTERNS)
    app_failure = any(pat in logs for pat in APP_FAILURE_PATTERNS)

    retry_count = get_retry_count(run_id)

    print(f"\nRun ID: {run_id}")
    print(f"PR Numbers: {[pr['number'] for pr in run['pull_requests']]}")
    print(f"Infra Failure: {infra_failure}")
    print(f"App Failure: {app_failure}")
    print(f"Retry Count: {retry_count}")

    # Record state (dry-run)
    record_retry(run, dry_run=True)

def get_retry_count(run_id):
    file_path = STATE_DIR / f"{run_id}.json"
    if file_path.exists():
        with open(file_path) as f:
            data = json.load(f)
            return data.get("retry_count", 0)
    return 0

def record_retry(run, dry_run=False):
    run_id = str(run["id"])
    file_path = STATE_DIR / f"{run_id}.json"
    current = get_retry_count(run_id)
    data = {
        "run_id": run_id,
        "repo": REPO,
        "pr_number": run["pull_requests"][0]["number"] if run["pull_requests"] else None,
        "retry_count": current + (0 if dry_run else 1),
        "last_retry_ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run
    }
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

if __name__ == "__main__":
    main()
