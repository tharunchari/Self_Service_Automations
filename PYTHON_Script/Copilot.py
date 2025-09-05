import requests

import pandas as pd
 
GITHUB_TOKEN = os.environ["CLASSIC_PAT"]

ENTERPRISE = "vitech"

HEADERS = {

    "Authorization": f"Bearer {TOKEN}",

    "Accept": "application/vnd.github+json"

}
 
def github_api(url):

    resp = requests.get(url, headers=HEADERS)

    resp.raise_for_status()

    return resp.json()
 
rows = []
 
# 1. Get all orgs in enterprise

orgs = github_api(f"https://api.github.com/enterprises/{ENTERPRISE}/orgs")
 
for org in orgs:

    org_login = org["login"]
 
    # 2. Get Copilot users in org

    usage = github_api(f"https://api.github.com/orgs/{org_login}/copilot/usage")
 
    for user in usage.get("breakdown", []):

        username = user.get("login")

        if not username:

            continue
 
        # 3. Get all repos in org

        repos = github_api(f"https://api.github.com/orgs/{org_login}/repos?per_page=100")
 
        for repo in repos:

            repo_name = repo["name"]
 
            # 4. Get last commit for that user in this repo

            commit_url = f"https://api.github.com/repos/{org_login}/{repo_name}/commits?author={username}&per_page=1"

            commits = github_api(commit_url)
 
            if isinstance(commits, list) and commits:

                commit = commits[0]["commit"]

                last_date = commit["committer"]["date"]

                last_email = commit["committer"]["email"]

            else:

                last_date = last_email = None
 
            rows.append({

                "User login": username,

                "Organization / repository": f"{org_login}/{repo_name}",

                "Last pushed date": last_date,

                "Last pushed email": last_email

            })
 
# 5. Save to Excel

df = pd.DataFrame(rows)

df.to_excel("enterprise_copilot_usage.xlsx", index=False)

 
