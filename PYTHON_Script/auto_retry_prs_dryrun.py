import os
import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import boto3
import zipfile
import io

# =============================
# Config
# =============================
TOKEN = os.environ.get("ORG_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json"
}
ORG = "vitechsystems"
REPOS_TO_SCAN = ["CoreAdmin"]  # Add more repos here as needed
MAX_RUNS_TO_CHECK = 200        # Increase this for heavy activity
SNS_TOPIC = os.environ.get("SNS_TOPIC")

INFRA_FAILURE_PATTERNS = [
    "lost communication",
    "runner unexpectedly disconnected",
    "received a shutdown signal",
    "the operation was canceled",
    "job canceled",
    "terminated by spot interruption",
    "connect error",
    "connection error",
    "network error",
    "failed to connect",
    "the self-hosted runner lost communication"
]

APP_FAILURE_PATTERNS = [
    "test failed",
    "lint",
    "compilation error"
]

STATE_DIR = Path(".github/ci-retry-state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

sns_client = boto3.client("sns")

# =============================
# REST API Helpers
# =============================

def get_failed_workflow_runs(repo, limit=MAX_RUNS_TO_CHECK):
    """
    Paginate to fetch up to `limit` most recent failed workflow runs triggered by PR.
    """
    runs = []
    page = 1
    print(f"Fetching up to {limit} recent failed PR workflow runs for repo: {repo}")
    while len(runs) < limit:
        url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs"
        params = {
            "status": "completed",
            "conclusion": "failure",
            "per_page": 50,
            "page": page
        }
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"Warning: Could not fetch failed workflow runs for repo {repo}: {resp.status_code} {resp.text}")
            break
        all_runs = resp.json().get("workflow_runs", [])
        pr_runs = [run for run in all_runs if run.get("event") == "pull_request"]
        runs.extend(pr_runs)
        if len(all_runs) < 50:
            break
        page += 1
    print(f"Found {len(runs)} failed PR workflow runs.")
    return runs[:limit]

def fetch_associated_pr_link(repo, run):
    """
    Try to determine PR links for this workflow run.
    """
    pr_links = []
    # First, try the direct 'pull_requests' field (sometimes not filled)
    if run.get("pull_requests"):
        for pr in run["pull_requests"]:
            if "number" in pr:
                pr_links.append(f"https://github.com/{ORG}/{repo}/pull/{pr['number']}")
    # If not found, use the head SHA to look up open PRs with that commit
    if not pr_links and run.get("head_sha"):
        pr_url = f"https://api.github.com/repos/{ORG}/{repo}/pulls"
        qs = {"state": "open", "head": f"{ORG}:{run.get('head_branch')}"}
        resp = requests.get(pr_url, headers=HEADERS, params=qs, timeout=10)
        if resp.status_code == 200:
            for pr in resp.json():
                if pr.get("head") and pr["head"].get("sha") == run.get("head_sha"):
                    pr_links.append(pr["html_url"])
    return ", ".join(pr_links) if pr_links else "Not a PR run"

def log_contains_patterns_and_debug(repo, run_id, infra_patterns, app_patterns):
    """
    Download zipped logs, scan for patterns line by line.
    Returns: "infra", "app", or None, and also prints up to 20 lines of logs for debug if no pattern matched.
    """
    url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/logs"
    matched = None
    log_sample = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=90, stream=True)
        resp.raise_for_status()
        with io.BytesIO(resp.content) as b, zipfile.ZipFile(b) as z:
            for filename in z.namelist():
                with z.open(filename) as f:
                    for i, raw_line in enumerate(f):
                        try:
                            line = raw_line.decode(errors='ignore')
                        except Exception:
                            continue
                        lower_line = line.lower()
                        # Print all lines containing fail, error, connect for debug
                        if any(word in lower_line for word in ('fail', 'error', 'connect', 'network')):
                            print(f"[log-match] {line.strip()}")
                        for pattern in infra_patterns:
                            if pattern in lower_line:
                                print(f"Matched INFRA pattern: '{pattern}' in line: {line.strip()}")
                                matched = "infra"
                        for pattern in app_patterns:
                            if pattern in lower_line:
                                print(f"Matched APP pattern: '{pattern}' in line: {line.strip()}")
                                matched = "app"
                        if len(log_sample) < 20:
                            log_sample.append(line.strip())
    except Exception as e:
        print(f"Failed to scan logs for {repo}/{run_id}: {e}")
    return matched, log_sample

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

# =============================
# Classification etc.
# =============================

def classify_run(repo, run, dry_run=True):
    run_id = run["id"]
    pr_link = fetch_associated_pr_link(repo, run)
    print(f"Analyzing run ID: {run_id} | PR Link: {pr_link} | Name: {run['name']} | Event: {run.get('event')}")
    pattern_hit, log_sample = log_contains_patterns_and_debug(repo, run_id, INFRA_FAILURE_PATTERNS, APP_FAILURE_PATTERNS)
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

    record_retry(run, repo, dry_run)
    # Always print log sample to workflow logs for all runs
    print(f"==== Log sample for run {run_id} in {repo} ====")
    print("PR Link:", pr_link)
    print("Matched reason:", reason)
    for log_line in log_sample:
        print(log_line)
    print("==== END Log sample ====")

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

# =============================
# Plain Text SNS Report
# =============================

def send_sns_summary(failures, total_repos):
    # Only send if there is at least one real infra or app failure!
    real = [f for f in failures if f["reason"] in ("Infra Failure", "App Failure")]
    if not SNS_TOPIC or not real:
        print("No infra/app failures or SNS_TOPIC not set. Skipping email notification.")
        return

    total_failures = len(real)
    infra_failures = sum(1 for f in real if f["reason"] == "Infra Failure")
    app_failures = sum(1 for f in real if f["reason"] == "App Failure")

    lines = [
        "Org-Wide PR Failures Report\n",
        "Summary:",
        f"- Org: {ORG}",
        f"- Total Repos: {total_repos}",
        f"- Total Failures: {total_failures}",
        f"- Infra Failures: {infra_failures}",
        f"- App Failures: {app_failures}",
        "\nFailed PRs / Workflows:"
    ]

    for f in real:
        lines.append(
            f"Repo: {f['repo']}\n"
            f"  PR Link: {f['pr_link']}\n"
            f"  Reason: {f['reason']}\n"
            f"  Retriggered: {f['retriggered']}\n"
        )

    text_report = "\n".join(lines)
    print("="*40 + "\n" + text_report + "\n" + "="*40)

    sns_client.publish(
        TopicArn=SNS_TOPIC,
        Subject="Org-Wide PR Failures Report",
        Message=text_report
    )
    print("SNS email sent.")

# =============================
# MAIN FUNCTION
# =============================

def main():
    dry_run = True  # Set to False to enable reruns
    print(f"{'Dry-Run' if dry_run else 'LIVE'} Org-Wide Auto-Retry for org: {ORG}")

    repos = REPOS_TO_SCAN
    print(f"Repos to scan: {repos}")
    all_failures = []

    for repo in repos:
        print(f"\nScanning repo: {repo}")
        failed_runs = get_failed_workflow_runs(repo, limit=MAX_RUNS_TO_CHECK)
        if not failed_runs:
            print("No failed workflow runs found.")
            continue
        for run in failed_runs:
            failure_info = classify_run(repo, run, dry_run=dry_run)
            all_failures.append(failure_info)

    send_sns_summary(all_failures, total_repos=len(repos))

    if not all_failures:
        print("No failures detected across selected repos.")

if __name__ == "__main__":
    main()
