#!/usr/bin/env python3
"""
github_codecommit_compare_ses.py

Compare latest commit SHA on default branch between GitHub and AWS CodeCommit
for vitechsystems and vitechinfra. Sends a beautifully formatted HTML email
via AWS SES (us-east-1). CodeCommit queries run in us-west-2.

Dry-run mode prints output without sending.
"""

import os
import sys
import time
import requests
import boto3
from botocore.exceptions import ClientError

# ---------------- CONFIGURATION ----------------
AWS_REGION_CODECOMMIT = "us-west-2"
AWS_REGION_SES = "us-east-1"

SES_FROM = "do-not-reply@vitechinc.com"
SES_TO = "v3atlassianops@vitechinc.com"

GITHUB_ORG_1 = "vitechsystems"
GITHUB_ORG_2 = "vitechinfra"

EXCLUDED_REPOS_VITECHSYSTEMS = ["hello-world", "github-runner-testing"]
DRY_RUN = False  # ✅ set False to actually send email

# ---------------- ENVIRONMENT VARIABLES ----------------
GITHUB_TOKEN_1 = os.getenv("GITHUB_TOKEN_1")
GITHUB_TOKEN_2 = os.getenv("GITHUB_TOKEN_2")

GITHUB_API_BASE = "https://api.github.com"
PER_PAGE = 100
REQUEST_TIMEOUT = 15
# -------------------------------------------------------


def exit_if_missing_credentials():
    missing = []
    if not GITHUB_TOKEN_1:
        missing.append("GITHUB_TOKEN_1")
    if not GITHUB_TOKEN_2:
        missing.append("GITHUB_TOKEN_2")
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


# ---------------- GITHUB HELPERS ----------------
def get_github_repos(org, token):
    """Return list of repos for a GitHub org."""
    repos = []
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "v3atlassianops-script"
    }
    page = 1
    while True:
        url = f"{GITHUB_API_BASE}/orgs/{org}/repos?per_page={PER_PAGE}&page={page}&type=all"
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            print(f"[ERROR] GitHub API {org} returned {resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json()
        if not data:
            break
        repos.extend([r.get("name") for r in data if r.get("name")])
        if len(data) < PER_PAGE:
            break
        page += 1
    return repos


def get_github_default_branch_and_sha(org, repo, token):
    """Return (default_branch, sha) for a GitHub repo."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "v3atlassianops-script"
    }
    repo_url = f"{GITHUB_API_BASE}/repos/{org}/{repo}"
    r = requests.get(repo_url, headers=headers, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        return None, f"Error: {r.status_code}"
    repo_data = r.json()
    default_branch = repo_data.get("default_branch")
    commit_url = f"{GITHUB_API_BASE}/repos/{org}/{repo}/commits/{default_branch}"
    cr = requests.get(commit_url, headers=headers, timeout=REQUEST_TIMEOUT)
    if cr.status_code != 200:
        return default_branch, f"Error: {cr.status_code}"
    commit_data = cr.json()
    sha = commit_data.get("sha", "UnknownSHA")
    return default_branch, sha


# ---------------- CODECOMMIT HELPERS ----------------
def get_codecommit_default_branch_and_sha(repo_name, aws_region):
    """Return (default_branch, commitId) for CodeCommit repo."""
    client = boto3.client("codecommit", region_name=aws_region)
    try:
        repo_resp = client.get_repository(repositoryName=repo_name)
        metadata = repo_resp.get("repositoryMetadata", {})
        default_branch = metadata.get("defaultBranch")
        if not default_branch:
            return None, "NoDefaultBranch"
        br = client.get_branch(repositoryName=repo_name, branchName=default_branch)
        commit_id = br.get("branch", {}).get("commitId", "UnknownCommitId")
        return default_branch, commit_id
    except ClientError as e:
        return None, f"Error: {e.response.get('Error', {}).get('Message', str(e))}"


# ---------------- REPORT BUILDERS ----------------
def build_html_table(title, rows):
    """Return an HTML table for mismatches."""
    html = f"<h3>{title}</h3>"
    if not rows:
        return html + "<p>No mismatches found ✅</p>"
    html += """
    <table>
      <tr><th>Repository</th><th>GitHub Commit ID</th><th>CodeCommit Commit ID</th></tr>
    """
    for repo, gh, cc in rows:
        gh_short = gh[:7] if gh else "-"
        cc_short = cc[:7] if cc else "-"
        html += f"<tr><td>{repo}</td><td>{gh}</td><td>{cc}</td></tr>"
    html += "</table><br>"
    return html


def build_html_body(org1_rows, org2_rows):
    """Compose full HTML email body with inline CSS."""
    style = """
    <style>
      body { font-family: Arial, sans-serif; background: #f9f9f9; color: #333; padding: 20px; }
      table { border-collapse: collapse; width: 100%; background: #fff; margin-top: 10px; }
      th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
      th { background: #0073e6; color: #fff; }
      tr:nth-child(even) { background: #f2f2f2; }
      h2 { color: #0073e6; }
      p { margin: 6px 0; }
    </style>
    """
    header = f"<h2>GitHub vs CodeCommit Commit Comparison Report</h2><p>Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>"
    table1 = build_html_table(f"{GITHUB_ORG_1} - Mismatched Repos (Latest Commits)", org1_rows)
    table2 = build_html_table(f"{GITHUB_ORG_2} - Mismatched Repos (Latest Commits)", org2_rows)
    footer = "<p style='font-size:12px;color:#888;'>Sent automatically via GitHub Actions using AWS SES</p>"
    return f"<html><head>{style}</head><body>{header}{table1}{table2}{footer}</body></html>"


# ---------------- SES EMAIL ----------------
def send_email_ses(subject, html_body):
    ses = boto3.client("ses", region_name=AWS_REGION_SES)
    try:
        response = ses.send_email(
            Source=SES_FROM,
            Destination={"ToAddresses": [SES_TO]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Html": {"Data": html_body}}
            }
        )
        print("✅ Email sent via SES:", response["MessageId"])
    except Exception as e:
        print("❌ Failed to send email via SES:", str(e))


# ---------------- MAIN ----------------
def main():
    exit_if_missing_credentials()
    print("Starting GitHub <-> CodeCommit comparison...\n")

    repos1 = [r for r in get_github_repos(GITHUB_ORG_1, GITHUB_TOKEN_1) if r not in EXCLUDED_REPOS_VITECHSYSTEMS]
    repos2_all = get_github_repos(GITHUB_ORG_2, GITHUB_TOKEN_2)
    repos2 = [r for r in repos2_all if r not in repos1]

    mismatches1 = []
    mismatches2 = []

    for repo in repos1:
        gh_branch, gh_sha = get_github_default_branch_and_sha(GITHUB_ORG_1, repo, GITHUB_TOKEN_1)
        cc_branch, cc_sha = get_codecommit_default_branch_and_sha(repo, AWS_REGION_CODECOMMIT)
        if gh_sha != cc_sha:
            mismatches1.append([repo, gh_sha, cc_sha])

    for repo in repos2:
        gh_branch, gh_sha = get_github_default_branch_and_sha(GITHUB_ORG_2, repo, GITHUB_TOKEN_2)
        cc_branch, cc_sha = get_codecommit_default_branch_and_sha(repo, AWS_REGION_CODECOMMIT)
        if gh_sha != cc_sha:
            mismatches2.append([repo, gh_sha, cc_sha])

    html_body = build_html_body(mismatches1, mismatches2)
    subject = "GitHub Backup: GitHub vs CodeCommit Commit Comparison Report"

    if DRY_RUN:
        print("----- DRY RUN -----")
        print(html_body)
        print("-------------------")
    else:
        send_email_ses(subject, html_body)


if __name__ == "__main__":
    main()
