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
REPOS_TO_SCAN = ["CoreAdmin"]  # Add more repos here later
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

def get_open_prs(repo):
    prs = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{ORG}/{repo}/pulls"
        resp = requests.get(url, headers=HEADERS, params={"state": "open", "per_page": 50, "page": page}, timeout=20)
        if resp.status_code != 200:
            print(f"Warning: Could not fetch PRs for repo {repo}: {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        if not data:
            break
        for pr in data:
            prs.append({"number": pr["number"], "url": pr["html_url"]})
        if len(data) < 50:
            break
        page += 1
    return prs

def get_failed_workflow_runs(repo):
    runs = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs"
        # Filter for runs on PRs and with 'failure' conclusion/status
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

def fetch_run_logs(repo, run_id):
    try:
        url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/logs"
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        logs_text = ""
        for filename in z.namelist():
            logs_text += z.read(filename).decode(errors='ignore')
        return logs_text
    except Exception as e:
        print(f"Failed to fetch logs for {repo}/{run_id}: {e}")
        return ""

def rerun_workflow(repo, run_id):
    url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/rerun"
    resp = requests.post(url, headers=HEADERS)
    if resp.status_code == 201:
        print(f"Workflow run {run_id} in repo {repo} rerun triggered successfully.")
        return True
    else:
        print(f"Failed to rerun workflow {run_id} for {repo}: {resp.text}")
        return False

# -----------------------------
# Classification etc.
# -----------------------------

def classify_run(repo, run, dry_run=True):
    run_id = run["id"]
    pr_link = None
    if run.get("pull_requests"):
        pr_nums = [pr["number"] for pr in run["pull_requests"] if "number" in pr]
        pr_link = ", ".join([f"https://github.com/{ORG}/{repo}/pull/{num}" for num in pr_nums]) or "Not a PR run"
    else:
        pr_link = "Not a PR run"

    logs = fetch_run_logs(repo, run_id)

    reason = "Unknown"
    color = "#FFFF99"
    retriggered = "No"
    retrigger_color = "#CCCCCC"

    for pattern in INFRA_FAILURE_PATTERNS:
        if pattern.lower() in logs.lower():
            reason = "Infra Failure"
            color = "#FF6666"
            if not dry_run:
                if rerun_workflow(repo, run_id):
                    retriggered = "Yes"
                    retrigger_color = "#00CC00"
            break
    else:
        for pattern in APP_FAILURE_PATTERNS:
            if pattern.lower() in logs.lower():
                reason = "App Failure"
                color = "#66CCFF"
                break
        else:
            reason = "Unknown Failure"
            color = "#FFFF99"

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
