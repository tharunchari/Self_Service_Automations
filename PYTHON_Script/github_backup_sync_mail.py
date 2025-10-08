#!/usr/bin/env python3
"""
github_codecommit_compare.py

Compare latest commit SHA on default branch between GitHub and AWS CodeCommit
for two orgs: vitechsystems and vitechinfra. Output only mismatches in a
single SNS email (two tables: vitechsystems then vitechinfra).

Config at top. Secrets via environment variables.
"""

import os
import sys
import time
import requests
import boto3
from botocore.exceptions import ClientError

# Optional niceties: tabulate (if available)
try:
    from tabulate import tabulate
except Exception:
    tabulate = None


# ------------------ CONFIGURATION (Edit here) ------------------
AWS_REGION = "us-west-2"
AWS_REGION_SNS = "us-east-1"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd"
GITHUB_ORG_1 = "vitechsystems"
GITHUB_ORG_2 = "vitechinfra"
# Repos to exclude from vitechsystems (same as your ansible)
EXCLUDED_REPOS_VITECHSYSTEMS = ["CoreAdmin", "Nextgen"]

# Dry run: if True -> do not send SNS; just print output
DRY_RUN = False

# ------------------ SECRETS / ENV (set these in your workflow) ----------
GITHUB_TOKEN_1 = os.getenv("GITHUB_TOKEN_1")  # token for org1 (or same token)
GITHUB_TOKEN_2 = os.getenv("GITHUB_TOKEN_2")  # token for org2
# AWS creds should be provided via environment or instance profile for boto3
# (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION) or role.

# ------------------ SETTINGS ------------------
GITHUB_API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 15  # seconds for HTTP calls
PER_PAGE = 100
# ---------------------------------------------------------------------


def exit_if_missing_credentials():
    missing = []
    if not GITHUB_TOKEN_1:
        missing.append("GITHUB_TOKEN_1")
    if not GITHUB_TOKEN_2:
        missing.append("GITHUB_TOKEN_2")
    # boto3 will use environment / instance role; we only warn if AWS_REGION missing
    if not AWS_REGION:
        missing.append("AWS_REGION")
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def get_github_repos(org, token):
    """Return list of repo names for a GitHub org (paginated)."""
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
            print(f"[ERROR] Request to GitHub failed for org={org} page={page}: {e}")
            break

        if resp.status_code != 200:
            print(f"[ERROR] GitHub API returned {resp.status_code} for {org} (page {page}): {resp.text[:400]}")
            break

        try:
            data = resp.json()
        except ValueError:
            print(f"[ERROR] Failed to JSON-decode GitHub response for {org} (page {page}).")
            break

        if not isinstance(data, list):
            print(f"[ERROR] Unexpected GitHub response type for {org} page {page}: {type(data)} - {data}")
            break

        if not data:
            # no more pages
            break

        repos.extend([r.get("name") for r in data if r.get("name")])
        if len(data) < PER_PAGE:
            break
        page += 1
        time.sleep(0.1)  # small throttle

    return repos


def get_github_default_branch_and_sha(org, repo, token):
    """Return (default_branch, sha) for the default branch of a GitHub repo.
       On error returns (None, "Error: ...")"""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "v3atlassianops-script"
    }
    repo_url = f"{GITHUB_API_BASE}/repos/{org}/{repo}"
    try:
        r = requests.get(repo_url, headers=headers, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        return None, f"Error: {e}"

    if r.status_code != 200:
        return None, f"Error: {r.status_code} {r.text.splitlines()[0][:200]}"

    try:
        repo_data = r.json()
    except ValueError:
        return None, "Error: invalid JSON from repo API"

    default_branch = repo_data.get("default_branch")
    if not default_branch:
        return None, "NoDefaultBranch"

    commit_url = f"{GITHUB_API_BASE}/repos/{org}/{repo}/commits/{default_branch}"
    try:
        cr = requests.get(commit_url, headers=headers, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        return default_branch, f"Error: {e}"

    if cr.status_code != 200:
        return default_branch, f"Error: {cr.status_code} {cr.text.splitlines()[0][:200]}"

    try:
        commit_data = cr.json()
    except ValueError:
        return default_branch, "Error: invalid JSON from commit API"

    sha = commit_data.get("sha") or commit_data.get("commit", {}).get("sha") or "UnknownSHA"
    return default_branch, sha


def get_codecommit_default_branch_and_sha(repo_name, aws_region):
    """Return (default_branch, commitId) for CodeCommit repo using boto3.
       On error return (None, 'Error: ...')"""
    client = boto3.client("codecommit", region_name=aws_region)
    try:
        repo_resp = client.get_repository(repositoryName=repo_name)
    except ClientError as e:
        return None, f"Error: {e.response.get('Error', {}).get('Message', str(e))}"
    except Exception as e:
        return None, f"Error: {e}"

    metadata = repo_resp.get("repositoryMetadata", {})
    default_branch = metadata.get("defaultBranch")
    if not default_branch:
        # no default branch set
        return None, "NoDefaultBranch"

    # now fetch branch info
    try:
        br = client.get_branch(repositoryName=repo_name, branchName=default_branch)
    except ClientError as e:
        return default_branch, f"Error: {e.response.get('Error', {}).get('Message', str(e))}"
    except Exception as e:
        return default_branch, f"Error: {e}"

    commit_id = br.get("branch", {}).get("commitId")
    if not commit_id:
        return default_branch, "UnknownCommitId"

    return default_branch, commit_id


def build_table_text(title, rows):
    """Build nice text table. Uses tabulate if available, else produces markdown-style table."""
    if not rows:
        return f"\n=== {title} ===\nNo mismatches found.\n"

    headers = ["Repository", "GitHub Commit (sha)", "CodeCommit Commit (sha)"]
    if tabulate:
        return f"\n=== {title} ===\n" + tabulate(rows, headers=headers, tablefmt="github") + "\n"
    # fallback format: markdown-like
    lines = []
    lines.append(f"\n=== {title} ===")
    # header
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    lines.append(header_line)
    lines.append(sep_line)
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    lines.append("")  # trailing newline
    return "\n".join(lines)


def send_sns_message(subject, message, topic_arn, aws_region):
    sns = boto3.client("sns", region_name=aws_region)
    try:
        resp = sns.publish(TopicArn=topic_arn, Subject=subject, Message=message)
        return resp
    except Exception as e:
        print(f"[ERROR] Failed to send SNS: {e}")
        return None


def main():
    exit_if_missing_credentials()
    print("Starting GitHub <-> CodeCommit commit comparison...")
    # 1) Fetch repos
    print(f"Fetching repos for {GITHUB_ORG_1} ...")
    repos1 = get_github_repos(GITHUB_ORG_1, GITHUB_TOKEN_1)
    # filter excluded
    repos1_filtered = [r for r in repos1 if r not in EXCLUDED_REPOS_VITECHSYSTEMS]
    print(f"Found {len(repos1)} repos in {GITHUB_ORG_1}, {len(repos1_filtered)} after exclusions.")

    print(f"Fetching repos for {GITHUB_ORG_2} ...")
    repos2 = get_github_repos(GITHUB_ORG_2, GITHUB_TOKEN_2)
    # keep only unique repos in org2 not present in org1
    repos2_unique = [r for r in repos2 if r not in repos1_filtered]
    print(f"Found {len(repos2)} repos in {GITHUB_ORG_2}, {len(repos2_unique)} unique after excluding org1 repos.")

    # 2) Compare commits per repo for org1
    mismatches_org1 = []
    for repo in repos1_filtered:
        print(f"[{GITHUB_ORG_1}] checking repo: {repo}")
        gh_branch, gh_sha = get_github_default_branch_and_sha(GITHUB_ORG_1, repo, GITHUB_TOKEN_1)
        cc_branch, cc_sha = get_codecommit_default_branch_and_sha(repo, AWS_REGION)
        # consider mismatch if strings differ
        if gh_sha != cc_sha:
            mismatches_org1.append([repo, gh_sha, cc_sha])

    # 3) Compare commits per repo for org2_unique
    mismatches_org2 = []
    for repo in repos2_unique:
        print(f"[{GITHUB_ORG_2}] checking repo: {repo}")
        gh_branch, gh_sha = get_github_default_branch_and_sha(GITHUB_ORG_2, repo, GITHUB_TOKEN_2)
        cc_branch, cc_sha = get_codecommit_default_branch_and_sha(repo, AWS_REGION)
        if gh_sha != cc_sha:
            mismatches_org2.append([repo, gh_sha, cc_sha])

    # 4) Build message
    header = f"GitHub vs CodeCommit Commit Comparison Report\nGenerated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    table1 = build_table_text(f"{GITHUB_ORG_1} (mismatches)", mismatches_org1)
    table2 = build_table_text(f"{GITHUB_ORG_2} (mismatches - unique to org2)", mismatches_org2)
    footer = "\nThanks,\nv3atlassianops\n"
    message_body = header + table1 + "\n" + table2 + footer

    print("\n----- REPORT -----\n")
    print(message_body)
    print("\n----- END REPORT -----\n")

    # 5) Send SNS (or dry-run)
    if DRY_RUN:
        print("[DRY RUN] Not sending SNS message. Set DRY_RUN = False to send.")
    else:
        print("Sending SNS message...")
        resp = send_sns_message("GitHub vs CodeCommit Commit Comparison Report", message_body, SNS_TOPIC_ARN, AWS_REGION_SNS)
        if resp:
            print("SNS publish response:", resp)
        else:
            print("SNS publish failed.")


if __name__ == "__main__":
    main()
