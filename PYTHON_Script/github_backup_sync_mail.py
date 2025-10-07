#!/usr/bin/env python3
import boto3
import subprocess
import os
import json
from tabulate import tabulate

# ------------------- CONFIGURATION -------------------
AWS_REGION = "us-west-2"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd"
GITHUB_ORG_1 = "vitechsystems"
GITHUB_ORG_2 = "vitechinfra"

# Excluded repos for vitechsystems
EXCLUDED_REPOS_VITECHSYSTEMS = ["CoreAdmin", "Nextgen", "CPF_PRODUCT_L5"]

# Secrets passed from workflow
GITHUB_TOKEN_1 = os.environ.get("GITHUB_PASSWORD_1")
GITHUB_TOKEN_2 = os.environ.get("GITHUB_PASSWORD_2")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
CODECOMMIT_USERNAME = os.environ.get("CODECOMMIT_USERNAME")
CODECOMMIT_PASSWORD = os.environ.get("CODECOMMIT_PASSWORD")

# ------------------- FUNCTIONS -------------------

def get_github_repos(org_name, token):
    """Fetch list of repositories from GitHub org using REST API."""
    print(f"Fetching repositories from GitHub org: {org_name}")
    repos = []
    page = 1
    while True:
        cmd = [
            "curl", "-s", "-H", f"Authorization: token {token}",
            f"https://api.github.com/orgs/{org_name}/repos?per_page=100&page={page}"
        ]
        output = subprocess.check_output(cmd, text=True)
        data = json.loads(output)
        if not data:
            break
        repos.extend([r["name"] for r in data])
        page += 1
    print(f"Total repos fetched from {org_name}: {len(repos)}")
    return repos


def get_latest_commit(repo_name, org_name, git_token, username, codecommit_user, codecommit_pass):
    """Get latest commit IDs from GitHub and CodeCommit."""
    github_url = f"https://{username}:{git_token}@github.com/{org_name}/{repo_name}.git"
    codecommit_url = f"https://{codecommit_user}:{codecommit_pass}@git-codecommit.{AWS_REGION}.amazonaws.com/v1/repos/{repo_name}"

    try:
        github_commit = subprocess.check_output(
            f"git ls-remote {github_url} | head -n1 | awk '{{print $1}}'",
            shell=True, text=True).strip()
    except subprocess.CalledProcessError:
        github_commit = "Error"

    try:
        codecommit_commit = subprocess.check_output(
            f"git ls-remote {codecommit_url} | head -n1 | awk '{{print $1}}'",
            shell=True, text=True).strip()
    except subprocess.CalledProcessError:
        codecommit_commit = "Error"

    return github_commit, codecommit_commit


def compare_and_generate(org_name, repos, token):
    """Compare commits for a given organization and return mismatched table."""
    print(f"\nComparing commits for {org_name}...")
    diff_table = []
    for repo in repos:
        github_commit, codecommit_commit = get_latest_commit(
            repo, org_name, token, GITHUB_USERNAME, CODECOMMIT_USERNAME, CODECOMMIT_PASSWORD
        )
        if github_commit != codecommit_commit:
            diff_table.append([repo, github_commit, codecommit_commit, "Not Matched"])
    print(f"{len(diff_table)} repos with mismatched commits in {org_name}")
    return diff_table


def send_sns_report(org_name, diff_table):
    """Send SNS report with mismatched commit details."""
    sns_client = boto3.client("sns", region_name=AWS_REGION)
    if diff_table:
        message_body = (
            f"{org_name} Repositories Commit Comparison:\n\n"
            + tabulate(diff_table, headers=["Repository", "GitHub Commit", "CodeCommit Commit", "Status"], tablefmt="github")
            + "\n\nThanks,\nv3atlassianops"
        )
    else:
        message_body = f"All repositories in {org_name} have matching commits.\n\nThanks,\nv3atlassianops"

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"GitHub vs CodeCommit Commit Comparison Report - {org_name}",
        Message=message_body
    )
    print(f"SNS report sent for {org_name}")


# ------------------- MAIN SCRIPT -------------------

def main():
    print("Starting GitHub vs CodeCommit Commit Comparison...")

    # ---- vitechsystems ----
    vitechsystems_repos = get_github_repos(GITHUB_ORG_1, GITHUB_TOKEN_1)
    vitechsystems_repos = [r for r in vitechsystems_repos if r not in EXCLUDED_REPOS_VITECHSYSTEMS]
    vitechsystems_diff = compare_and_generate(GITHUB_ORG_1, vitechsystems_repos, GITHUB_TOKEN_1)
    send_sns_report(GITHUB_ORG_1, vitechsystems_diff)

    # ---- vitechinfra ----
    vitechinfra_repos = get_github_repos(GITHUB_ORG_2, GITHUB_TOKEN_2)
    vitechinfra_diff = compare_and_generate(GITHUB_ORG_2, vitechinfra_repos, GITHUB_TOKEN_2)
    send_sns_report(GITHUB_ORG_2, vitechinfra_diff)

    print("Comparison completed successfully.")


if __name__ == "__main__":
    main()
