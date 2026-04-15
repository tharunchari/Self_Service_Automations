import requests
import time
import argparse
import os

# === HEADERS ===
GITHUB_TOKEN = os.environ.get("PROD_FINE_GRAINED_PAT")

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28"
}


# === GET ALL REPOS ===
def get_all_repos(org):
    repos = []
    page = 1

    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}"
        response = requests.get(url, headers=HEADERS)

        if response.status_code != 200:
            print(f"Failed to fetch repos: {response.text}")
            break

        data = response.json()
        if not data:
            break

        repos.extend([repo["name"] for repo in data])
        page += 1
        time.sleep(0.5)

    return repos


# === UPDATE WEBHOOK SECRET ===
def update_webhook(org, repo, webhook_url, new_secret, dry_run):
    hooks_url = f"https://api.github.com/repos/{org}/{repo}/hooks"

    response = requests.get(hooks_url, headers=HEADERS)

    if response.status_code != 200:
        print(f"[{repo}] Failed to fetch hooks: {response.text}")
        return

    hooks = response.json()

    if not hooks:
        print(f"[{repo}] No webhooks found.")
        return

    for hook in hooks:
        existing_url = hook.get("config", {}).get("url", "")

        if existing_url.startswith(webhook_url):
            hook_id = hook["id"]
            update_url = f"{hooks_url}/{hook_id}"

            updated_config = hook["config"]
            updated_config["secret"] = new_secret  # 🔥 KEY CHANGE

            payload = {
                "config": updated_config
            }

            if dry_run:
                print(f"[DRY-RUN] {repo} → Would update secret")
                return

            update_resp = requests.patch(update_url, headers=HEADERS, json=payload)

            if update_resp.status_code == 200:
                print(f"[{repo}] ✅ Secret updated successfully")
            else:
                print(f"[{repo}] ❌ Failed: {update_resp.text}")

            return

    print(f"[{repo}] No matching webhook found")


# === MAIN ===
def main():
    parser = argparse.ArgumentParser(description="Update GitHub webhook secret")

    parser.add_argument("--org", required=True)
    parser.add_argument("--scope", required=True)  # single | multiple | all
    parser.add_argument("--repos", default="")
    parser.add_argument("--webhook-url", required=True)
    parser.add_argument("--new-secret", required=True)
    parser.add_argument("--dry-run", type=lambda x: x.lower() == "true", default=True)

    args = parser.parse_args()

    org = args.org
    scope = args.scope.lower()
    webhook_url = args.webhook_url
    new_secret = args.new_secret
    dry_run = args.dry_run

    # === DETERMINE REPOS ===
    if scope == "all":
        repos = get_all_repos(org)
        print(f"Processing ALL repos: {len(repos)}")

    elif scope == "multiple":
        repos = [r.strip() for r in args.repos.split(",") if r.strip()]
        print(f"Processing MULTIPLE repos: {repos}")

    elif scope == "single":
        repos = [args.repos.strip()]
        print(f"Processing SINGLE repo: {repos}")

    else:
        print("Invalid scope. Use: single | multiple | all")
        return

    # === PROCESS ===
    for repo in repos:
        update_webhook(org, repo, webhook_url, new_secret, dry_run)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
