import requests

import os

import pandas as pd
 
GITHUB_TOKEN = os.environ["CLASSIC_PAT"]

ENTERPRISE = "vitech"
 
HEADERS = {

    "Authorization": f"Bearer {GITHUB_TOKEN}",

    "Accept": "application/vnd.github+json"

}
 
def github_api(url):

    resp = requests.get(url, headers=HEADERS)

    resp.raise_for_status()

    return resp.json()
 
rows = []
 
# ✅ Get Copilot usage at enterprise level

usage = github_api(f"https://api.github.com/enterprises/{ENTERPRISE}/copilot/usage")
 
for user in usage.get("breakdown", []):

    username = user.get("login")

    org = user.get("organization")

    if not username:

        continue
 
    # Example: pull last commit for that user from a known repo

    # (to expand: iterate through repos if needed)

    repo = "CoreAdmin"

    commit_url = f"https://api.github.com/repos/{org}/{repo}/commits?author={username}&per_page=1"

    commits = github_api(commit_url)
 
    if isinstance(commits, list) and commits:

        commit = commits[0]["commit"]

        last_date = commit["committer"]["date"]

        last_email = commit["committer"]["email"]

    else:

        last_date = last_email = None
 
    rows.append({

        "User login": username,

        "Organization / repository": f"{org}/{repo}",

        "Last pushed date": last_date,

        "Last pushed email": last_email

    })
 
df = pd.DataFrame(rows)

df.to_excel("enterprise_copilot_usage.xlsx", index=False)

 
