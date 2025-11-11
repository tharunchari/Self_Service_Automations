#!/usr/bin/env python3
"""
update_team_role.py

Usage (from workflow):
  python3 PYTHON_Script/update_team_role.py --dry-run=True

Environment variables (provided by the workflow):
  TARGET                : "Repository" or "Organization"
  REPO_NAME             : comma separated repo names (owner/repo or repo if org assumed ownership)
  ORG_NAME              : organization name (choice in workflow)
  TEAM_NAME             : (optional) human-friendly team name to find in the org (e.g. 'Vitech DevOps Team')
  TEAM_SLUG             : (optional) team slug to use directly (e.g. 'vitech-devops-svc-account'). If provided, team lookup is skipped.
  CURRENT_ROLE          : current role to look for (pull/push/Super User)
  NEW_ROLE              : new role to apply (pull/push/Super User)
  PROD_FINE_GRAINED_PAT : token for vitechsystems organization
  INFRA_FINE_GRAINED_PAT: token for vitechinfra organization

Notes:
- Script only uses the permissions: "pull", "push", "Super Users" (exact API payload uses "Super Users").
- It will never set "admin" or any other permission.
- If TEAM_SLUG is set, it is used directly and the script does not try to search teams.
"""

import os
import sys
import argparse
import requests
import boto3
import botocore
from datetime import datetime, timezone
from typing import List, Dict, Tuple

# ---------------------------
# Config / env
# ---------------------------
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd")

TARGET = os.environ.get("TARGET", "").strip()
GITHUB_REPOS = os.environ.get("REPO_NAME", "").strip()
GITHUB_ORG = os.environ.get("ORG_NAME", "").strip()
TEAM_NAME = os.environ.get("TEAM_NAME", "").strip()
TEAM_SLUG_ENV = os.environ.get("TEAM_SLUG", "").strip()   # NEW: accept team slug directly
CURRENT_ROLE = os.environ.get("CURRENT_ROLE", "").strip()
NEW_ROLE = os.environ.get("NEW_ROLE", "").strip()
TOKEN_PROD = os.environ.get("PROD_FINE_GRAINED_PAT")
TOKEN_INFRA = os.environ.get("INFRA_FINE_GRAINED_PAT")

SES_SOURCE_EMAIL = os.environ.get("SES_SOURCE_EMAIL", "noreply@vitechsystems.com")
SES_RECIPIENTS = [e.strip() for e in os.environ.get("SES_RECIPIENTS", "ops-team@vitechsystems.com").split(",") if e.strip()]

# ---------------------------
# Role mapping: only allowed roles
# ---------------------------
ROLE_MAP = {
    "pull": "pull",
    "push": "push",
    # Accept many variants of "super user" and map them to exact "Super Users" string used in your curl example.
    "super user": "Super Users",
    "super users": "Super Users",
    "super_user": "Super Users",
    "superuser": "Super Users",
    "superusers": "Super Users",
    "super-users": "Super Users",
    "super": "Super Users",
}

ALLOWED_CANONICAL = set(ROLE_MAP.values())  # {'pull','push','Super Users'}

def normalize_role_to_api(role: str) -> str:
    """Normalize user-provided role to exact API string we should use."""
    if not role:
        return ""
    key = role.strip().lower()
    return ROLE_MAP.get(key, key)  # if mapping exists use it, else return lowered key

# ---------------------------
# Helpers: API & pagination
# ---------------------------
def pick_token(org_name: str) -> str:
    if not org_name:
        return TOKEN_PROD or TOKEN_INFRA
    if org_name.lower().startswith("vitechinfra"):
        return TOKEN_INFRA or TOKEN_PROD
    return TOKEN_PROD or TOKEN_INFRA

def gh_headers(token: str):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vitech-tools-update-team-role-script"
    }

def github_get(session: requests.Session, url: str, params=None) -> requests.Response:
    resp = session.get(url, params=params)
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub GET {url} failed: {resp.status_code} {resp.text}")
    return resp

def github_put(session: requests.Session, url: str, json_payload=None) -> requests.Response:
    resp = session.put(url, json=json_payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub PUT {url} failed: {resp.status_code} {resp.text}")
    return resp

def paginate(session: requests.Session, url: str, params=None) -> List[Dict]:
    results = []
    page = 1
    per_page = 100
    while True:
        p = params.copy() if params else {}
        p.update({"per_page": per_page, "page": page})
        resp = github_get(session, url, params=p)
        data = resp.json()
        if not isinstance(data, list):
            break
        results.extend(data)
        if len(data) < per_page:
            break
        page += 1
    return results

# ---------------------------
# Mailing via SES
# ---------------------------
def send_email_via_ses(subject: str, html_body: str) -> Tuple[bool, str]:
    ses = boto3.client("ses", region_name=AWS_REGION)
    try:
        resp = ses.send_email(
            Source=SES_SOURCE_EMAIL,
            Destination={"ToAddresses": SES_RECIPIENTS},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Html": {"Data": html_body}}
            }
        )
        return True, f"SES message id: {resp.get('MessageId')}"
    except botocore.exceptions.ClientError as e:
        return False, f"SES send failed: {str(e)}"

def publish_sns(message: str, subject: str):
    sns = boto3.client("sns", region_name=AWS_REGION)
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject=subject)
    except Exception as e:
        print(f"[WARN] SNS publish failed: {e}")

# ---------------------------
# Core logic
# ---------------------------
def find_team_in_org(session: requests.Session, org: str, team_name: str) -> Dict:
    """Find team by name or slug (case-insensitive) and return team object including slug and id."""
    url = f"https://api.github.com/orgs/{org}/teams"
    teams = paginate(session, url)
    for t in teams:
        if t.get("name", "").lower() == team_name.lower() or t.get("slug", "").lower() == team_name.lower():
            return t
    raise RuntimeError(f"Team named '{team_name}' not found in org '{org}'")

def get_org_repos(session: requests.Session, org: str) -> List[Dict]:
    url = f"https://api.github.com/orgs/{org}/repos"
    return paginate(session, url)

def get_repo_current_permission_for_team(session: requests.Session, org: str, team_slug: str, owner: str, repo: str) -> str:
    url = f"https://api.github.com/orgs/{org}/teams/{team_slug}/repos/{owner}/{repo}"
    resp = session.get(url)
    if resp.status_code == 404:
        return "none"
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to get permission for team/{team_slug} on {owner}/{repo}: {resp.status_code} {resp.text}")
    payload = resp.json()
    perm = payload.get("permission")
    if perm:
        return perm
    perms = payload.get("permissions")
    if isinstance(perms, dict):
        if perms.get("admin"):
            return "admin"
        if perms.get("push"):
            return "push"
        if perms.get("pull"):
            return "pull"
    return "unknown"

def update_team_permission_on_repo(session: requests.Session, org: str, team_slug: str, owner: str, repo: str, permission: str) -> None:
    url = f"https://api.github.com/orgs/{org}/teams/{team_slug}/repos/{owner}/{repo}"
    payload = {"permission": permission}
    resp = session.put(url, json=payload)
    if resp.status_code not in (204, 200):
        raise RuntimeError(f"Update failed for {owner}/{repo} -> {resp.status_code} {resp.text}")

def build_html_table(rows: List[Dict], title: str) -> str:
    now = datetime.now(timezone.utc).astimezone().isoformat()
    html = f"<html><body><h2>{title}</h2><p>Run: {now}</p>"
    html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>"
    html += "<thead><tr><th>Repository</th><th>Current Role</th><th>Requested Role</th><th>Result</th></tr></thead><tbody>"
    for r in rows:
        html += "<tr>"
        html += f"<td>{r.get('repo')}</td>"
        html += f"<td>{r.get('current_role')}</td>"
        html += f"<td>{r.get('requested_role')}</td>"
        html += f"<td>{r.get('result')}</td>"
        html += "</tr>"
    html += "</tbody></table></body></html>"
    return html

# ---------------------------
# Main flow
# ---------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", dest="dry_run", required=True, help="True/False")
    args = parser.parse_args()
    dry_run = str(args.dry_run).lower() in ("true", "1", "yes", "y")

    # validate env inputs
    if TARGET not in ("Repository", "Organization"):
        print(f"[ERROR] TARGET must be 'Repository' or 'Organization'. Got: {TARGET}")
        sys.exit(2)
    if not GITHUB_ORG:
        print("[ERROR] ORG_NAME is required")
        sys.exit(2)
    if not CURRENT_ROLE:
        print("[ERROR] CURRENT_ROLE is required")
        sys.exit(2)
    if not NEW_ROLE:
        print("[ERROR] NEW_ROLE is required")
        sys.exit(2)

    # normalize the roles and validate they are in allowed set
    curr_role_api = normalize_role_to_api(CURRENT_ROLE)
    new_role_api = normalize_role_to_api(NEW_ROLE)

    if curr_role_api not in ALLOWED_CANONICAL and curr_role_api != "":
        print(f"[ERROR] CURRENT_ROLE '{CURRENT_ROLE}' normalized to '{curr_role_api}' is not one of allowed roles: {ALLOWED_CANONICAL}")
        sys.exit(2)
    if new_role_api not in ALLOWED_CANONICAL and new_role_api != "":
        print(f"[ERROR] NEW_ROLE '{NEW_ROLE}' normalized to '{new_role_api}' is not one of allowed roles: {ALLOWED_CANONICAL}")
        sys.exit(2)

    token = pick_token(GITHUB_ORG)
    if not token:
        print("[ERROR] No GitHub token available in env. Provide PROD_FINE_GRAINED_PAT or INFRA_FINE_GRAINED_PAT.")
        sys.exit(2)

    print(f"[INFO] TARGET={TARGET}, ORG={GITHUB_ORG}, dry_run={dry_run}")
    print(f"[INFO] CURRENT_ROLE -> '{curr_role_api}' | NEW_ROLE -> '{new_role_api}'")

    session = requests.Session()
    session.headers.update(gh_headers(token))

    # Determine team slug to use:
    team_slug = None
    if TEAM_SLUG_ENV:
        team_slug = TEAM_SLUG_ENV
        print(f"[INFO] Using provided TEAM_SLUG='{team_slug}' (skipping team lookup).")
    else:
        if not TEAM_NAME:
            print("[ERROR] Neither TEAM_SLUG nor TEAM_NAME provided. Provide one of them.")
            sys.exit(2)
        try:
            team = find_team_in_org(session, GITHUB_ORG, TEAM_NAME)
            team_slug = team.get("slug")
            print(f"[INFO] Found team slug by name: {team_slug} (id={team.get('id')})")
        except Exception as e:
            msg = f"Failed to find team '{TEAM_NAME}' in org '{GITHUB_ORG}': {e}"
            print("[ERROR] " + msg)
            publish_sns(msg, f"Update Team Role: Team Not Found {GITHUB_ORG}/{TEAM_NAME}")
            sys.exit(3)

    # collect target repos
    target_repos = []
    if TARGET == "Repository":
        if not GITHUB_REPOS:
            print("[ERROR] REPO_NAME must be provided when TARGET=Repository")
            sys.exit(2)
        for r in GITHUB_REPOS.split(","):
            r = r.strip()
            if not r:
                continue
            if "/" in r:
                owner, repo = r.split("/", 1)
            else:
                owner = GITHUB_ORG
                repo = r
            target_repos.append({"owner": owner, "repo": repo})
    else:
        print(f"[INFO] Fetching all repos for org {GITHUB_ORG} via pagination")
        repos = get_org_repos(session, GITHUB_ORG)
        for item in repos:
            owner = item.get("owner", {}).get("login", GITHUB_ORG)
            repo = item.get("name")
            target_repos.append({"owner": owner, "repo": repo})

    print(f"[INFO] Found {len(target_repos)} target repositories to check/update")

    results = []
    for entry in target_repos:
        owner = entry["owner"]
        repo = entry["repo"]
        repo_name = f"{owner}/{repo}"

        try:
            current_raw = get_repo_current_permission_for_team(session, GITHUB_ORG, team_slug, owner, repo)
        except Exception as e:
            current_raw = f"error: {e}"
            results.append({"repo": repo_name, "current_role": current_raw, "requested_role": new_role_api, "result": f"Failed to get current role: {e}"})
            print(f"[ERROR] {repo_name} -> Failed to get current role: {e}")
            continue

        # normalize current permission to our canonical set if possible
        current_norm = current_raw
        if isinstance(current_raw, str):
            low = current_raw.strip().lower()
            current_norm = ROLE_MAP.get(low, current_raw)

        # Determine update condition:
        do_update = False
        note = ""

        if curr_role_api and current_norm.lower() == curr_role_api.lower():
            if new_role_api.lower() != curr_role_api.lower():
                do_update = True
            else:
                note = "No update required (current == requested new role)"
        else:
            note = f"Skipping: current permission '{current_norm}' != expected CURRENT_ROLE '{curr_role_api}'"

        if dry_run:
            result_text = f"DRYRUN: would update {current_norm} -> {new_role_api}" if do_update else f"DRYRUN: no action. {note}"
            results.append({"repo": repo_name, "current_role": current_norm, "requested_role": new_role_api, "result": result_text})
            print(f"[DRYRUN] {repo_name}: {result_text}")
            continue

        if do_update:
            try:
                update_team_permission_on_repo(session, GITHUB_ORG, team_slug, owner, repo, new_role_api)
                result_text = f"Updated {current_norm} -> {new_role_api}"
                print(f"[OK] {repo_name}: {result_text}")
            except Exception as e:
                result_text = f"Update failed: {e}"
                print(f"[ERROR] {repo_name}: {result_text}")
        else:
            result_text = note or "No update performed"
            print(f"[SKIP] {repo_name}: {result_text}")

        results.append({"repo": repo_name, "current_role": current_norm, "requested_role": new_role_api, "result": result_text})

    # Send report via SES (HTML)
    title = f"Update Team Role Report for team '{team_slug}' in org '{GITHUB_ORG}' (dry_run={dry_run})"
    html = build_html_table(results, title)
    subject = f"[GitHub] Team Role Update Report - {GITHUB_ORG}/{team_slug} - dry_run={dry_run}"

    ok, info = send_email_via_ses(subject, html)
    if ok:
        print(f"[INFO] SES email sent: {info}")
    else:
        print(f"[WARN] SES failed: {info}. Publishing to SNS as fallback.")
        text = subject + "\n\n" + "\n".join([f"{r['repo']}\t{r['current_role']}\t{r['requested_role']}\t{r['result']}" for r in results])
        publish_sns(text, subject)

if __name__ == "__main__":
    main()
