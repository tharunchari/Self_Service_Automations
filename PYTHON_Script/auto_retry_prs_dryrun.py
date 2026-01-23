import os
import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import boto3
import zipfile
import io

# -----------------------------
# Config
# -----------------------------
TOKEN = os.environ.get("ORG_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json"
}
ORG = "vitechsystems"
REPOS_TO_SCAN = ["CoreAdmin"]  # Add more repos here as needed
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 2))
SNS_TOPIC = os.environ.get("SNS_TOPIC")

INFRA_FAILURE_PATTERNS = [
    "runner unexpectedly disconnected",
    "received a shutdown signal",
    "lost communication with the server",
    "the operation was canceled",
    "job canceled",
    "terminated by spot interruption"
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
# REST API Helpers
# -----------------------------

def get_failed_workflow_runs(repo):
    runs = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs"
        params = {
            "status": "completed",
            "conclusion": "failure",
            "per_page": 30,
            "page": page
        }
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"Warning: Could not fetch failed workflow runs for repo {repo}: {resp.status_code} {resp.text}")
            break
        page_runs = resp.json().get("workflow_runs", [])
        for run in page_runs:
            runs.append(run)
        if len(page_runs) < 30:
            break
        page += 1
    return runs

def log_contains_patterns(repo, run_id, infra_patterns, app_patterns):
    """
    Download zipped logs, scan for patterns line by line.
    Returns: "infra", "app", or None
    """
    url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/logs"
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
            resp.raise_for_status()
            with io.BytesIO(resp.content) as b, zipfile.ZipFile(b) as z:
                for filename in z.namelist():
                    with z.open(filename) as f:
                        for raw_line in f:
                            try:
                                line = raw_line.decode(errors='ignore').lower()
                            except Exception:
                                continue
                            for pattern in infra_patterns:
                                if pattern.lower() in line:
                                    return "infra"
                            for pattern in app_patterns:
                                if pattern.lower() in line:
                                    return "app"
            return None
        except Exception as e:
            print(f"Failed to scan logs for {repo}/{run_id} (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None

def rerun_workflow(repo, run_id):
    url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/rerun"
    try:
        resp = requests.post(url, headers=HEADERS)
        if resp.status_code == 201:
            print(f"Workflow run {run_id} in repo {repo} rerun triggered successfully.")
            return True
        else:
            print(f"Failed to rerun workflow {run_id} for {repo}: {resp.text}")
            return False
    except Exception as e:
        print(f"Failed to rerun workflow {run_id} in {repo}: {e}")
        return False

# -----------------------------
# Classification etc.
# -----------------------------

def classify_run(repo, run, dry_run=True):
    run_id = run["id"]
    # Try to get PR link(s)
    pr_links = []
    if run.get("pull_requests"):
        for pr in run["pull_requests"]:
            if "number" in pr:
                pr_links.append(f"https://github.com/{ORG}/{repo}/pull/{pr['number']}")
    pr_link = ", ".join(pr_links) if pr_links else "Not a PR run"

    pattern_hit = log_contains_patterns(repo, run_id, INFRA_FAILURE_PATTERNS, APP_FAILURE_PATTERNS)
    reason = "Unknown Failure"
    color = "#FFFF99"
    retriggered = "No"
    retrigger_color = "#CCCCCC"

    if pattern_hit == "infra":
        reason = "Infra Failure"
        color = "#FF6666"
        if not dry_run:
            if rerun_workflow(repo, run_id):
                retriggered = "Yes"
                retrigger_color = "#00CC00"
    elif pattern_hit == "app":
        reason = "App Failure"
        color = "#66CCFF"
    # else: keep default "Unknown Failure"

    record_retry(run, repo, dry_run)
    return {
        "repo": repo,
        "pr_link": pr_link,
        "reason": reason,
        "color": color,
        "retriggered": retriggered,
        "retrigger_color": retrigger_color
    }

def record_retry(run, repo, dry_run=True):
    run_id = str(run["id"])
    file_path = STATE_DIR / f"{run_id}.json"
    data = {
        "run_id": run_id,
        "repo": repo,
        "retry_count": 0 if dry_run else 1,
        "last_retry_ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run
    }
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

def send_sns_summary(failures, total_repos):
    if not SNS_TOPIC or not failures:
        print("No failures or SNS_TOPIC not set. Skipping email notification.")
        return

    total_failures = len(failures)
    infra_failures = sum(1 for f in failures if "Infra" in f["reason"])
    app_failures = sum(1 for f in failures if "App" in f["reason"])

    html = f"""
    <html>
    <body>
    <h2>Org-Wide PR Failures Report</h2>
    <h3>Summary</h3>
    <table border='1' style='border-collapse: collapse; width: 60%;'>
        <tr style='background-color:#4CAF50; color:white;'>
            <th>Org</th><th>Total Repos</th><th>Total Failures</th><th>Infra Failures</th><th>App Failures</th>
        </tr>
        <tr>
            <td>{ORG}</td>
            <td>{total_repos}</td>
            <td>{total_failures}</td>
            <td>{infra_failures}</td>
            <td>{app_failures}</td>
        </tr>
    </table>
    <br>
    <h3>Failed PRs / Workflows</h3>
    <table border='1' style='border-collapse: collapse; width: 100%;'>
        <tr style='background-color:#4CAF50; color:white;'>
            <th>Repo</th><th>PR Link</th><th>Reason</th><th>Retriggered</th>
        </tr>
    """
    for f in failures:
        html += f"""
        <tr style='background-color:{f['color']}'>
            <td>{f['repo']}</td>
            <td><a href="{f['pr_link']}">{f['pr_link']}</a></td>
            <td>{f['reason']}</td>
            <td style='background-color:{f['retrigger_color']}; color:white;'>{f['retriggered']}</td>
        </tr>
        """
    html += "</table></body></html>"

    sns_client.publish(
        TopicArn=SNS_TOPIC,
        Subject="Org-Wide PR Failures Report",
        Message=html
    )
    print("SNS email sent.")

# -----------------------------
# MAIN FUNCTION
# -----------------------------

def main():
    dry_run = True  # Set to False to enable reruns
    print(f"{'Dry-Run' if dry_run else 'LIVE'} Org-Wide Auto-Retry for org: {ORG}")

    repos = REPOS_TO_SCAN
    print(f"Repos to scan: {repos}")
    all_failures = []

    for repo in repos:
        print(f"\nScanning repo: {repo}")
        failed_runs = get_failed_workflow_runs(repo)
        if not failed_runs:
            print("No failed workflow runs found.")
            continue
        for run in failed_runs:
            failure_info = classify_run(repo, run, dry_run=dry_run)
            all_failures.append(failure_info)

    if all_failures:
        send_sns_summary(all_failures, total_repos=len(repos))
    else:
        print("No failures detected across selected repos.")

if __name__ == "__main__":
    main()
