import os
import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import boto3
from urllib.parse import quote_plus

# -----------------------------
# Config
# -----------------------------
TOKEN = os.environ.get("ORG_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
ORG = "vitechsystems"
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 2))
SNS_TOPIC = os.environ.get("SNS_TOPIC")
DRY_RUN = True  # Change to False to enable retry

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
# GraphQL helpers with rate-limit handling
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
# Fetch all repos with pagination
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
# Fetch workflow runs + PRs with pagination
# -----------------------------
def get_repo_workflows(repo):
    all_failed_runs = []
    cursor = None
    while True:
        query = """
        query($org: String!, $repo: String!, $after: String) {
          repository(owner: $org, name: $repo) {
            pullRequests(first: 10, states: [OPEN, MERGED, CLOSED], after: $after) {
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
# Classify failure as Infra or App
# -----------------------------
def classify_run(run_info):
    run = run_info["run"]
    repo = run_info["repo"]
    prs = run_info["prs"]
    run_url = run["url"]

    pr_link = ", ".join([pr["url"] for pr in prs]) if prs else "Not a PR run"
    logs = fetch_run_logs(run_url)

    reason = "Unknown"
    color = "#FFFF99"  # default yellow

    for pattern in INFRA_FAILURE_PATTERNS:
        if pattern.lower() in logs.lower():
            reason = "Infra Failure"
            color = "#FF9999"  # red
            break
    else:
        for pattern in APP_FAILURE_PATTERNS:
            if pattern.lower() in logs.lower():
                reason = "App Failure"
                color = "#99CCFF"  # blue
                break
        else:
            reason = "Unknown Failure"
            color = "#FFFF99"

    record_retry(run, repo, dry_run=DRY_RUN)
    return {"repo": repo, "pr_link": pr_link, "reason": reason, "color": color}

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
# SNS notification (batch)
# -----------------------------
def send_sns_summary(failures, total_repos):
    if not SNS_TOPIC or not failures:
        print("No failures or SNS_TOPIC not set. Skipping email notification.")
        return

    # Summary counts
    infra_count = sum(1 for f in failures if f["reason"] == "Infra Failure")
    app_count = sum(1 for f in failures if f["reason"] == "App Failure")
    unknown_count = sum(1 for f in failures if f["reason"] == "Unknown Failure")

    # HTML top summary
    html_summary = f"""
    <html><body>
    <h2>Org-Wide PR Failures Dry-Run Report</h2>
    <table border='1' style='border-collapse: collapse; width: 50%;'>
        <tr style='background-color:#4CAF50; color:white;'>
            <th>Organization</th><th>Total Repos Scanned</th><th>Total Failures</th><th>Infra</th><th>App</th><th>Unknown</th>
        </tr>
        <tr style='text-align:center;'>
            <td>{ORG}</td>
            <td>{total_repos}</td>
            <td>{len(failures)}</td>
            <td>{infra_count}</td>
            <td>{app_count}</td>
            <td>{unknown_count}</td>
        </tr>
    </table>
    <br>
    """

    # HTML detailed table
    html_table = """
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

    message = html_summary + html_table

    sns_client.publish(
        TopicArn=SNS_TOPIC,
        Subject="Org-Wide PR Failures Report",
        Message=message
    )
    print("SNS email sent.")

# -----------------------------
# Main
# -----------------------------
def main():
    print(f"Dry-Run Org-Wide Auto-Retry for org: {ORG}")
    repos = get_all_repos()
    total_repos = len(repos)
    print(f"Total repos found: {total_repos}")
    all_failures = []

    for repo in repos:
        print(f"\nScanning repo: {repo}")
        failed_runs = get_repo_workflows(repo)
        if not failed_runs:
            print("No failed workflow runs found.")
            continue
        for run_info in failed_runs:
            failure_info = classify_run(run_info)
            all_failures.append(failure_info)

    if all_failures:
        send_sns_summary(all_failures, total_repos)
    else:
        print(f"No failures detected across org ({total_repos} repos).")

if __name__ == "__main__":
    main()
