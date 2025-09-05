import requests

import os

import pandas as pd
 
# === CONFIG ===

# Replace with your actual enterprise slug (from https://github.com/enterprises/<slug>)

ENTERPRISE = "vitech"
 
# Get token from GitHub Actions secret or local env

GITHUB_TOKEN = os.environ["CLASSIC_PAT"]
 
HEADERS = {

    "Authorization": f"Bearer {GITHUB_TOKEN}",

    "Accept": "application/vnd.github+json",

    "X-GitHub-Api-Version": "2022-11-28"

}
 
# === Helpers ===

def github_api(url):

    """Basic GitHub API GET with error handling."""

    resp = requests.get(url, headers=HEADERS)

    if resp.status_code != 200:

        print(f"⚠️ API error {resp.status_code} for {url}: {resp.text}")

        resp.raise_for_status()

    return resp.json()
 
def github_api_paginate(url):

    """Handles GitHub API pagination automatically."""

    results = []

    page = 1

    while True:

        resp = requests.get(f"{url}&page={page}", headers=HEADERS)

        if resp.status_code != 200:

            print(f"⚠️ API error {resp.status_code} for {url}: {resp.text}")

            break

        data = resp.json()

        if not data:

            break

        results.extend(data)

        page += 1

    return results
 
# === Main ===

rows = []
 
print(f"Fetching Copilot usage for enterprise: {ENTERPRISE} ...")
 
# 1. Get Copilot usage for enterprise

usage = github_api(f"https://api.github.com/enterprises/{ENTERPRISE}/copilot/usage")
 
for user in usage.get("breakdown", []):

    username = user.get("login")

    org = user.get("organization")

    if not username or not org:

        continue
 
    print(f"Processing user: {username} in org: {org}")
 
    # 2. Get all repos in org

    repos = github_api_paginate(f"https://api.github.com/orgs/{org}/repos?per_page=100")
 
    for repo in repos:

        repo_name = repo["name"]
 
        # 3. Get last commit for that user in this repo

        commit_url = f"https://api.github.com/repos/{org}/{repo_name}/commits?author={username}&per_page=1"

        commits = github_api(commit_url)
 
        if isinstance(commits, list) and commits:

            commit = commits[0]["commit"]

            last_date = commit["committer"]["date"]

            last_email = commit["committer"]["email"]

        else:

            last_date = last_email = None
 
        rows.append({

            "User login": username,

            "Organization / repository": f"{org}/{repo_name}",

            "Last pushed date": last_date,

            "Last pushed email": last_email

        })
 
# 4. Save to Excel

df = pd.DataFrame(rows)

output_file = "enterprise_copilot_usage.xlsx"

df.to_excel(output_file, index=False)
 
print(f"✅ Report generated: {output_file}")

 
