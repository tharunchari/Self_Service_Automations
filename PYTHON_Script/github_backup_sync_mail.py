#!/usr/bin/env python3
"""
github_codecommit_compare_html.py

Compare latest commit SHA on default branch between GitHub and AWS CodeCommit
for two orgs: vitechsystems and vitechinfra. Output mismatches in a
beautiful HTML-formatted SNS email (two tables: vitechsystems then vitechinfra).

Config at top. Secrets via environment variables.
"""

import os
import sys
import time
import requests
import boto3
import datetime
import json
from botocore.exceptions import ClientError

try:
    from tabulate import tabulate
except Exception:
    tabulate = None

# ------------------ CONFIGURATION ------------------
AWS_REGION = "us-west-2"          # for CodeCommit
AWS_REGION_SNS = "us-east-1"      # for SNS mail
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd"

GITHUB_ORG_1 = "vitechsystems"
GITHUB_ORG_2 = "vitechinfra"
EXCLUDED_REPOS_VITECHSYSTEMS = ["CoreAdmin", "Nextgen"]

DRY_RUN = False
# ----------------------------------------------------

GITHUB_TOKEN_1 = os.getenv("GITHUB_TOKEN_1")
GITHUB_TOKEN_2 = os.getenv("GITHUB_TOKEN_2")

GITHUB_API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 15
PER_PAGE = 100


def exit_if_missing_credentials():
    missing = []
    if not GITHUB_TOKEN_1:
        missing.append("GITHUB_TOKEN_1")
    if not GITHUB_TOKEN_2:
        missing.append("GITHUB_TOKEN_2")
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def get_github_repos(org, token):
    repos = []
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "v3atlassianops-script"
    }
    page = 1
    while True:
        url = f"{GITHUB_API_BASE}/orgs/{org}/repos?per_page={PER_PAGE}&page={page}&type=all"
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print(f"[ERROR] GitHub request failed for org={org} page={page}: {e}")
            break

        if resp.status_code != 200:
            print(f"[ERROR] GitHub API returned {resp.status_code} for {org} page={page}")
            break

        try:
            data = resp.json()
        except ValueError:
            print(f"[ERROR] Invalid JSON for {org} page={page}")
            break

        if not isinstance(data, list) or not data:
            break

        repos.extend([r.get("name") for r in data if r.get("name")])
        if len(data) < PER_PAGE:
            break
        page += 1
        time.sleep(0.1)

    return repos


def get_github_default_branch_and_sha(org, repo, token):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "v3atlassianops-script"
    }

    repo_url = f"{GITHUB_API_BASE}/repos/{org}/{repo}"
    try:
        r = requests.get(repo_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None, f"Error: {r.status_code}"
        repo_data = r.json()
    except Exception as e:
        return None, f"Error: {e}"

    default_branch = repo_data.get("default_branch")
    if not default_branch:
        return None, "NoDefaultBranch"

    commit_url = f"{GITHUB_API_BASE}/repos/{org}/{repo}/commits/{default_branch}"
    try:
        cr = requests.get(commit_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if cr.status_code != 200:
            return default_branch, f"Error: {cr.status_code}"
        commit_data = cr.json()
    except Exception as e:
        return default_branch, f"Error: {e}"

    sha = commit_data.get("sha") or "UnknownSHA"
    return default_branch, sha


def get_codecommit_default_branch_and_sha(repo_name, aws_region):
    client = boto3.client("codecommit", region_name=aws_region)
    try:
        repo_resp = client.get_repository(repositoryName=repo_name)
    except ClientError as e:
        return None, f"Error: {e.response['Error'].get('Message', str(e))}"
    except Exception as e:
        return None, f"Error: {e}"

    metadata = repo_resp.get("repositoryMetadata", {})
    default_branch = metadata.get("defaultBranch")
    if not default_branch:
        return None, "NoDefaultBranch"

    try:
        br = client.get_branch(repositoryName=repo_name, branchName=default_branch)
        commit_id = br.get("branch", {}).get("commitId", "UnknownCommitId")
    except Exception as e:
        return default_branch, f"Error: {e}"

    return default_branch, commit_id


def build_html_table(title, rows):
    """Return HTML section for mismatches."""
    if not rows:
        return f"<h4>{title}</h4><p>No mismatches found.</p>"

    if tabulate:
        table_html = tabulate(rows, headers=["Repository", "GitHub Commit (sha)", "CodeCommit Commit (sha)"], tablefmt="html")
    else:
        # fallback basic HTML table
        table_html = "<table border='1' cellspacing='0' cellpadding='5'><tr><th>Repository</th><th>GitHub Commit (sha)</th><th>CodeCommit Commit (sha)</th></tr>"
        for repo, gh, cc in rows:
            table_html += f"<tr><td>{repo}</td><td>{gh}</td><td>{cc}</td></tr>"
        table_html += "</table>"

    return f"<h4>{title}</h4>{table_html}"


def send_sns_html(subject, html_body):
    sns_client = boto3.client("sns", region_name=AWS_REGION_SNS)

    try:
        message = json.dumps({
            "default": "GitHub vs CodeCommit HTML report",
            "email": html_body
        })

        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message,
            MessageStructure="json"
        )
        print("✅ SNS HTML notification sent successfully")
    except Exception as e:
        print("❌ Failed to send SNS notification:", str(e))


def main():
    exit_if_missing_credentials()
    print("Starting GitHub <-> CodeCommit commit comparison...")

    repos1 = get_github_repos(GITHUB_ORG_1, GITHUB_TOKEN_1)
    repos1_filtered = [r for r in repos1 if r not in EXCLUDED_REPOS_VITECHSYSTEMS]

    repos2 = get_github_repos(GITHUB_ORG_2, GITHUB_TOKEN_2)
    repos2_unique = [r for r in repos2 if r not in repos1_filtered]

    mismatches_org1 = []
    for repo in repos1_filtered:
        gh_branch, gh_sha = get_github_default_branch_and_sha(GITHUB_ORG_1, repo, GITHUB_TOKEN_1)
        cc_branch, cc_sha = get_codecommit_default_branch_and_sha(repo, AWS_REGION)
        if gh_sha != cc_sha:
            mismatches_org1.append([repo, gh_sha, cc_sha])

    mismatches_org2 = []
    for repo in repos2_unique:
        gh_branch, gh_sha = get_github_default_branch_and_sha(GITHUB_ORG_2, repo, GITHUB_TOKEN_2)
        cc_branch, cc_sha = get_codecommit_default_branch_and_sha(repo, AWS_REGION)
        if gh_sha != cc_sha:
            mismatches_org2.append([repo, gh_sha, cc_sha])

    # ----- Build HTML -----
    generated_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #222;">
        <h3>GitHub vs CodeCommit Commit Comparison Report</h3>
        <p><b>Generated:</b> {generated_time}</p>
        {build_html_table(GITHUB_ORG_1 + " (mismatches)", mismatches_org1)}
        <br>
        {build_html_table(GITHUB_ORG_2 + " (mismatches - unique to org2)", mismatches_org2)}
        <br>
        <p>Thanks,<br><b>v3atlassianops</b></p>
    </body>
    </html>
    """

    print("----- HTML REPORT -----")
    print(html_body)
    print("------------------------")

    if DRY_RUN:
        print("[DRY RUN] Not sending SNS message.")
    else:
        send_sns_html("GitHub vs CodeCommit Commit Comparison Report", html_body)


if __name__ == "__main__":
    main()
