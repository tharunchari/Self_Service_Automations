import os
import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import boto3
from requests.exceptions import RequestException

# -----------------------------
# Config
# -----------------------------
TOKEN = os.environ.get("ORG_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}
ORG = "vitechsystems"
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 2))
SNS_TOPIC = os.environ.get("SNS_TOPIC")

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

STATE_DIR = Path(".github/ci-retry-state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

sns_client = boto3.client("sns")

# -----------------------------
# GitHub API helpers with retry
# -----------------------------
def github_get(url, max_attempts=5, backoff=2):
    """GET request with retry/backoff"""
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except RequestException as e:
            print(f"GitHub API request error (attempt {attempt+1}/{max_attempts}): {e}")
            time.sleep(backoff * (attempt + 1))
    print(f"Failed to fetch URL after {max_attempts} attempts: {url}")
    return None

def get_org_repos():
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{ORG}/repos?per_page=100&page={page}"
        data = github_get(url)
        if not data:
            break
        repos.extend([r["name"] for r in data])
        page += 1
    return repos

def get_failed_pr_runs(repo):
    url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs?event=pull_request&status=failure&per_page=100"
    data = github_get(url)
    if not data:
        return []
    return data.get("workflow_runs", [])

def get_run_logs(repo, run_id):
    url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/logs"
    data = github_get(url)
    if data is None:
        return ""
    return str(data).lower()  # convert to string for pattern search

# -----------------------------
# Failure classification
# -----------------------------
def classify_run(repo, run):
    run_id = str(run["id"])
    pr_numbers = [pr["number"] for pr in run.get("pull_requests", [])]
    if not pr_numbers:
        pr_link = "Not a PR run"
    else:
        pr_link = ", ".join([f"https://github.com/{ORG}/{repo}/pull/{n}" for n in pr_numbers])

    logs = get_run_logs(repo, run_id)
    infra_failure = any(pat in logs for pat in INFRA_FAILURE_PATTERNS)
    app_failure = any(pat in logs for pat in APP_FAILURE_PATTERNS)
    reason = []
    color = "#f0f0f0"
    if infra_failure:
        reason.append("Infra Failure")
        color = "#FF9999"  # red
    if app_failure:
        reason.append("App Failure")
        color = "#FFD966"  # yellow
    if not reason:
        reason = ["Unknown/Other Failure"]
        color = "#D9D9D9"  # grey

    print(f"Run ID: {run_id}")
    print(f"PR Link: {pr_link}")
    print(f"Reason: {', '.join(reason)}")

    record_retry(run, repo, dry_run=True)

    return {"repo": repo, "pr_link": pr_link, "reason": ", ".join(reason), "color": color}

# -----------------------------
# Retry state (dry-run)
# -----------------------------
def get_retry_count(run_id):
    file_path = STATE_DIR / f"{run_id}.json"
    if file_path.exists():
        with open(file_path) as f:
            data = json.load(f)
            return data.get("retry_count", 0)
    return 0

def record_retry(run, repo, dry_run=True):
    run_id = str(run["id"])
    file_path = STATE_DIR / f"{run_id}.json"
    current = get_retry_count(run_id)
    pr_number = run.get("pull_requests")[0]["number"] if run.get("pull_requests") else None
    data = {
        "run_id": run_id,
        "repo": repo,
        "pr_number": pr_number,
        "retry_count": current + (0 if dry_run else 1),
        "last_retry_ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run
    }
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

# -----------------------------
# SNS notification
# -----------------------------
def send_sns_summary(failures):
    if not SNS_TOPIC:
        print("SNS_TOPIC is not set. Skipping email notification.")
        return

    html_table = """
    <html>
    <body>
    <h2>Org-Wide PR Failures Dry-Run Report</h2>
    <table border='1' style='border-collapse: collapse; width: 100%;'>
        <tr style='background-color:#4CAF50; color:white;'>
            <th>Repo</th><th>PR Link</th><th>Reason</th>
        </tr>
    """
    for f in failures:
        html_table += f"""
        <tr style='background-color:{f['color']};'>
            <td>{f['repo']}</td>
            <td><a href="{f['pr_link']}">{f['pr_link']}</a></td>
            <td>{f['reason']}</td>
        </tr>
        """
    html_table += "</table></body></html>"

    sns_client.publish(
        TopicArn=SNS_TOPIC,
        Subject="Org-Wide PR Failures Report",
        Message=html_table
    )
    print("\nSNS notification sent with summary of failures.")

# -----------------------------
# Main
# -----------------------------
def main():
    print(f"Dry-Run Org-Wide Auto-Retry for org: {ORG}")
    repos = get_org_repos()
    print(f"Total repos found: {len(repos)}")
    all_failures = []

    for repo in repos:
        try:
            print(f"\nScanning repo: {repo}")
            runs = get_failed_pr_runs(repo)
            if not runs:
                print("No failed PR workflow runs found in this repo.")
                continue
            for run in runs:
                failure_info = classify_run(repo, run)
                if failure_info:
                    all_failures.append(failure_info)
        except Exception as e:
            print(f"Skipping repo {repo} due to error: {e}")

    if all_failures:
        send_sns_summary(all_failures)
    else:
        print("\nNo failures detected across org.")

# -----------------------------
# Entry point
# -----------------------------
if __name__ == "__main__":
    main()
