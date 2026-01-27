import os
import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import boto3
import zipfile
import io

TOKEN = os.environ.get("ORG_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json"
}
ORG = "vitechsystems"
REPOS_TO_SCAN = ["CoreAdmin"]
MAX_RUNS_TO_CHECK = 200  # process up to 100 failed PR workflow runs per repo
SNS_TOPIC = os.environ.get("SNS_TOPIC")

INFRA_FAILURE_PATTERNS = [
    "lost communication",
    "the self-hosted runner lost communication",
    "runner unexpectedly disconnected",
    "received a shutdown signal",
    "the operation was canceled",
    "job canceled",
    "terminated by spot interruption",
    "connect error",
    "connection error",
    "network error",
    "failed to connect"
]

APP_FAILURE_PATTERNS = [
    "test failed",
    "lint",
    "compilation error"
]

STATE_DIR = Path(".github/ci-retry-state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

sns_client = boto3.client("sns")

def get_failed_workflow_runs(repo, limit=MAX_RUNS_TO_CHECK):
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
    pr_links = []
    if run.get("pull_requests"):
        for pr in run["pull_requests"]:
            if "number" in pr:
                pr_links.append(f"https://github.com/{ORG}/{repo}/pull/{pr['number']}")
    if not pr_links and run.get("head_sha"):
        pr_url = f"https://api.github.com/repos/{ORG}/{repo}/pulls"
        qs = {"state": "open", "per_page": 100}
        pr_resp = requests.get(pr_url, headers=HEADERS, params=qs, timeout=10)
        if pr_resp.status_code == 200:
            for pr in pr_resp.json():
                if pr.get("head", {}).get("sha", "") == run.get("head_sha"):
                    pr_links.append(pr["html_url"])
    return ", ".join(pr_links) if pr_links else "Not a PR run"

def log_contains_patterns_and_debug(repo, run_id, infra_patterns, app_patterns):
    url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/logs"
    matched_infra = None
    matched_app = None
    log_sample = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=90, stream=True)
        resp.raise_for_status()
        with io.BytesIO(resp.content) as b, zipfile.ZipFile(b) as z:
            for filename in z.namelist():
                with z.open(filename) as f:
                    for raw_line in f:
                        try:
                            line = raw_line.decode(errors='ignore')
                        except Exception:
                            continue
                        lower_line = line.lower()
                        for pattern in infra_patterns:
                            if pattern in lower_line:
                                print(f"Matched INFRA pattern (LOG): '{pattern}' in line: {line.strip()}")
                                matched_infra = pattern
                        for pattern in app_patterns:
                            if pattern in lower_line:
                                print(f"Matched APP pattern (LOG): '{pattern}' in line: {line.strip()}")
                                matched_app = pattern
                        if len(log_sample) < 20:
                            log_sample.append(line.strip())
    except Exception as e:
        print(f"Failed to scan logs for {repo}/{run_id}: {e}")
    return matched_infra, matched_app, log_sample

def annotations_contains_infra(repo, run_id, infra_patterns):
    """Fetch job annotation fields and scan for infra patterns anywhere in any string field."""
    jobs_url = f"https://api.github.com/repos/{ORG}/{repo}/actions/runs/{run_id}/jobs"
    try:
        jobs_resp = requests.get(jobs_url, headers=HEADERS, timeout=30)
        jobs_resp.raise_for_status()
        jobs = jobs_resp.json().get("jobs", [])
        for job in jobs:
            # Check all string values in the job struct
            for key, val in job.items():
                if isinstance(val, str):
                    for pattern in infra_patterns:
                        if pattern in val.lower():
                            print(f"Matched INFRA pattern (ANNOTATION JOB FIELD): '{pattern}' in {key}: {val}")
                            return True, f"[Job field: {key}]"
            # Check top-level job failure_message
            if job.get("failure_message"):
                text = job.get("failure_message").lower()
                for pattern in infra_patterns:
                    if pattern in text:
                        print(f"Matched INFRA pattern (ANNOTATION failure_message): '{pattern}' in {text}")
                        return True, "[failure_message]"
            # Check all steps
            for step in job.get("steps", []):
                # Check all string values in the step
                for skey, sval in step.items():
                    if isinstance(sval, str):
                        for pattern in infra_patterns:
                            if pattern in sval.lower():
                                print(f"Matched INFRA pattern (ANNOTATION STEP FIELD): '{pattern}' in {skey}: {sval}")
                                return True, f"[Step field: {skey}]"
    except Exception as e:
        print(f"Failed to check annotations for run {run_id} in {repo}: {e}")
    return False, ""

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

def classify_run(repo, run, dry_run=True):
    run_id = run["id"]
    pr_link = fetch_associated_pr_link(repo, run)
    print(f"Analyzing run ID: {run_id} | PR Link: {pr_link} | Name: {run['name']} | Event: {run.get('event')}")
    # --- First, scan the zipped logs
    matched_infra, matched_app, log_sample = log_contains_patterns_and_debug(repo, run_id, INFRA_FAILURE_PATTERNS, APP_FAILURE_PATTERNS)
    reason = "Unknown Failure"
    color = "#FFFF99"
    retriggered = "No"
    retrigger_color = "#CCCCCC"

    # --- If not matched in logs, check annotations/jobs (full scan of all string fields)
    ann_infra, ann_context = (False, "")
    if not matched_infra:
        ann_infra, ann_context = annotations_contains_infra(repo, run_id, INFRA_FAILURE_PATTERNS)

    if matched_infra or ann_infra:
        reason = "Infra Failure"
        color = "#FF6666"
        if ann_context:
            print(f"Matched INFRA pattern in Job Annotation: {ann_context}")
        if not dry_run:
            if rerun_workflow(repo, run_id):
                retriggered = "Yes"
                retrigger_color = "#00CC00"
    elif matched_app:
        reason = "App Failure"
        color = "#66CCFF"

    record_retry(run, repo, dry_run)
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

def send_sns_summary(failures, total_repos, repo_name):
    # Only "Infra Failure" triggers email
    infra_only = [f for f in failures if f["reason"] == "Infra Failure"]
    if not SNS_TOPIC or not infra_only:
        print("No infra failures or SNS_TOPIC not set. Skipping email notification.")
        return

    total_failures = len(infra_only)
    lines = [
        f"CoreAdmin PR Infra Failures Report\n",
        "Summary:",
        f"- Org: {ORG}",
        f"- Repo: {repo_name}",
        f"- Total Infra Failures: {total_failures}",
        "\nInfra Failed PRs / Workflows:"
    ]

    for f in infra_only:
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
        Subject=f"CoreAdmin PR Infra Failures Report",
        Message=text_report
    )
    print("SNS email sent.")

def main():
    dry_run = True  # Change to False to enable rerun
    repos = REPOS_TO_SCAN
    print(f"Repos to scan: {repos}")
    for repo in repos:
        print(f"\nScanning repo: {repo}")
        all_failures = []
        failed_runs = get_failed_workflow_runs(repo, limit=MAX_RUNS_TO_CHECK)
        if not failed_runs:
            print("No failed workflow runs found.")
            continue
        for run in failed_runs:
            failure_info = classify_run(repo, run, dry_run=dry_run)
            all_failures.append(failure_info)
        send_sns_summary(all_failures, total_repos=1, repo_name=repo)
        if not all_failures:
            print("No failures detected in repo:", repo)

if __name__ == "__main__":
    main()
