import requests
import time
import argparse
import os

# === CONFIGURATION ===
GITHUB_TOKEN = os.environ.get("PROD_FINE_GRAINED_PAT")  # PAT from workflow env
ORG_NAME = "vitechsystems"
BASE_WEBHOOK_URL = "https://kix4g7xxor35kastx66mnxebta0hidqk.lambda-url.us-east-1.on.aws"
DRY_RUN_REPOS = ["EGID_PRODUCT_L5", "PACLife_PRODUCT_L5", "IPERS_PRODUCT_L5"]  # Replace with actual repo names for dry run

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28"
}

def get_all_repos():
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{ORG_NAME}/repos?per_page=100&page={page}"
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print(f"Failed to fetch repos: {response.text}")
            break
        data = response.json()
        if not data:
            break
        repos.extend(data)
        page += 1
        time.sleep(0.5)
    return [repo["name"] for repo in repos]

def update_webhook(repo_name):
    hooks_url = f"https://api.github.com/repos/{ORG_NAME}/{repo_name}/hooks"
    response = requests.get(hooks_url, headers=HEADERS)
    if response.status_code != 200:
        print(f"[{repo_name}] Failed to fetch hooks: {response.text}")
        return

    hooks = response.json()
    if not hooks:
        print(f"[{repo_name}] No webhooks found.")
        return

    for hook in hooks:
        hook_url = hook.get("config", {}).get("url", "")
        if hook_url.startswith(BASE_WEBHOOK_URL):
            hook_id = hook["id"]
            update_url = f"{hooks_url}/{hook_id}"
            updated_events = list(set(hook["events"] + ["pull_request_review_thread"]))
            payload = {
                "active": True,
                "events": updated_events,
                "config": hook["config"]
            }
            update_resp = requests.patch(update_url, headers=HEADERS, json=payload)
            if update_resp.status_code == 200:
                print(f"[{repo_name}] Webhook updated successfully.")
            else:
                print(f"[{repo_name}] Failed to update webhook: {update_resp.text}")
            return

    print(f"[{repo_name}] No matching webhook found.")

def main(dry_run):
    if dry_run:
        repos = DRY_RUN_REPOS
        print(f"Dry run mode: Updating webhooks in {repos}")
    else:
        repos = get_all_repos()
        print(f"Full run mode: Found {len(repos)} repositories.")

    for repo in repos:
        update_webhook(repo)
        time.sleep(0.5)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update GitHub webhooks across repositories.")
    parser.add_argument("--dry-run", type=lambda x: x.lower() == "true", default=False,
                        help="Run in dry mode (only update specific repos)")
    args = parser.parse_args()
    main(args.dry_run)
