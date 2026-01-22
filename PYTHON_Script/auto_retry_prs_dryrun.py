import os
import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import boto3

# -----------------------------
# Config
# -----------------------------
TOKEN = os.environ.get("ORG_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
ORG = "vitechsystems"
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
GRAPHQL_URL = "https://api.github.com/graphql"

# -----------------------------
# GraphQL helpers
# -----------------------------
def graphql_query(query, variables=None, max_attempts=5, backoff=2):
    for attempt in range(max_attempts):
        try:
            resp = requests.post(
                GRAPHQL_URL, headers=HEADERS, json={"query": query, "variables": variables}, timeout=20
            )
            if resp.status_code == 403 and 'X-RateLimit-Remaining' in resp.headers:
                remaining = int(resp.headers['X-RateLimit-Remaining'])
                reset_time = int(resp.headers['X-RateLimit-Reset'])
                if remaining == 0:
                    sleep_sec = max(reset_time - int(time.time()), 0) + 5
                    print(f"Rate limit exceeded, sleeping for {sleep_sec} seconds...")
                    time.sleep(sleep_sec)
                    continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"GraphQL request error (attempt {attempt+1}/{max_attempts}): {e}")
            time.sleep(backoff * (attempt + 1))
    print("Failed to complete GraphQL request.")
    return None

# -----------------------------
# Fetch all repos
# -----------------------------
def get_all_repos():
    repos = []
    cursor = None
    while True:
        query = """
        query($org: String!, $after: String) {
          organization(login: $org) {
            repositories(first: 50, after: $after, orderBy: {field: NAME, direction: ASC}) {
              nodes { name }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
        """
        variables = {"org": ORG, "after": cursor}
        result = graphql_query(query, variables)
        if not result:
            break
        nodes = result["data"]["organization"]["repositories"]["nodes"]
        repos.extend([r["name"] for r in nodes])
        page_info = result["data"]["organization"]["repositories"]["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return repos

# -----------------------------
# Fetch workflow runs per repo
# -----------------------------
def get_repo_workflows(repo):
    all_failed_runs = []
    cursor = None
    while True:
        query = """
        query($org: String!, $repo: String!, $after: String) {
          repository(owner: $org, name: $repo) {
            pullRequests(first: 10, states: [OPEN], after: $after) {
              nodes { number url }
              pageInfo { hasNextPage endCursor }
            }
            workflows(first: 5) {
              nodes {
                name
                runs(first: 10, statuses: FAILURE) {
                  nodes { id url conclusion createdAt checkSuite { workflowRun { id } } }
                  pageInfo { hasNextPage endCursor }
                }
              }
            }
          }
        }
        """
        variables = {"org": ORG, "repo": repo, "after": cursor}
        result = graphql_query(query, variables)
        if not result:
            break

        repo_data = result.get("data", {}).get("repository", {})
        prs = repo_data.get("pullRequests", {}).get("nodes", [])
        workflows = repo_data.get("workflows", {}).get("nodes", [])
        for wf in workflows:
            runs_info = wf.get("runs", {}).get("nodes", [])
            for run in runs_info:
                all_failed_runs.append({"run": run, "prs": prs, "repo": repo})

        page_info = repo_data.get("pullRequests", {}).get("pageInfo", {})
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
        else:
            break
    return all_failed_runs

# -----------------------------
# Fetch workflow run logs
# -----------------------------
def fetch_run_logs(run_url):
    try:
        parts = run_url.split("/")
        owner, repo, run_id = parts[3], parts[4], parts[-1]
        api_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        resp = requests.get(api_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        logs_text = ""
        for job in jobs:
            logs_text += job.get("name", "") + "\n"
            for step in job.get("steps", []):
                logs_text += step.get("name", "") + ": " + str(step.get("conclusion")) + "\n"
        return logs_text
    except Exception as e:
        print(f"Failed to fetch logs for {run_url}: {e}")
        return ""

# -----------------------------
# Retry workflow via GitHub API
# -----------------------------
def rerun_workflow(run_id, repo):
    owner = ORG
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/rerun"
    resp = requests.post(url, headers=HEADERS)
    if resp.status_code == 201:
        print(f"Workflow run {run_id} for {repo} rerun triggered successfully.")
        return True
    else:
        print(f"Failed to rerun workflow {run_id} for {repo}: {resp.text}")
        return False

# -----------------------------
# Classify failure and optionally rerun
# -----------------------------
def classify_run(run_info, dry_run=True):
    run = run_info["run"]
    repo = run_info["repo"]
    prs = run_info["prs"]
    run_url = run["url"]

    pr_link = ", ".join([pr["url"] for pr in prs]) if prs else "Not a PR run"
    logs = fetch_run_logs(run_url)

    reason = "Unknown"
    color = "#FFFF99"
    retriggered = "No"
    retrigger_color = "#CCCCCC"

    for pattern in INFRA_FAILURE_PATTERNS:
        if pattern.lower() in logs.lower():
            reason = "Infra Failure"
            color = "#FF6666"  # red
            if not dry_run:
                if rerun_workflow(run["id"], repo):
                    retriggered = "Yes"
                    retrigger_color = "#00CC00"  # green
            break
    else:
        for pattern in APP_FAILURE_PATTERNS:
            if pattern.lower() in logs.lower():
                reason = "App Failure"
                color = "#66CCFF"  # blue
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

# -----------------------------
# Retry state
# -----------------------------
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

# -----------------------------
# SNS Notification
# -----------------------------
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
        <tr style='background-color:{f['color']};'>
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
# Main
# -----------------------------
def main():
    dry_run = True  # Set to False to enable reruns
    print(f"{'Dry-Run' if dry_run else 'LIVE'} Org-Wide Auto-Retry for org: {ORG}")

    repos = get_all_repos()
    print(f"Total repos found: {len(repos)}")
    all_failures = []

    for repo in repos:
        print(f"\nScanning repo: {repo}")
        failed_runs = get_repo_workflows(repo)
        if not failed_runs:
            print("No failed workflow runs found.")
            continue
        for run_info in failed_runs:
            failure_info = classify_run(run_info, dry_run=dry_run)
            all_failures.append(failure_info)

    if all_failures:
        send_sns_summary(all_failures, total_repos=len(repos))
    else:
        print("No failures detected across org.")

if __name__ == "__main__":
    main()
