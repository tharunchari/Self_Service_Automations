#!/usr/bin/env python3
"""
majesco_github_backup_sync_mail.py

Daily sync checker between GitHub and AWS CodeCommit.

What it does
------------
1. Auto-discovers EVERY GitHub org reachable by the provided tokens
   (GITHUB_TOKEN_1, GITHUB_TOKEN_2, ... or an explicit GITHUB_ORGS list).
2. Enumerates all repos in each org.
3. For every repo, reads the latest commit SHA on the default branch.
4. Looks up the matching AWS CodeCommit repository, whose name follows the
   convention  "<org>_<repo>",  and reads its default-branch commit id.
5. Compares the two commit ids:
       - match     -> ignored
       - mismatch  -> collected into a per-org table
6. If anything is out of sync (or ALWAYS_SEND is set) a formatted HTML email
   is sent via AWS SES, with ONE table per org.

Configuration (environment variables)
--------------------------------------
  GITHUB_TOKEN_1, GITHUB_TOKEN_2, ...   GitHub PATs (any count, auto-detected 1..20)
  GITHUB_ORGS         Optional. Comma-separated explicit org list. If set, it
                      overrides auto-discovery (tokens are still used to read).
  EXCLUDED_ORGS       Comma-separated org logins to skip (e.g. "sandbox,demo")
  EXCLUDED_REPOS      Comma-separated repos to skip. Either "repo" (matches in
                      any org) or "org/repo" (matches one org).
  SKIP_ARCHIVED       "true" to skip archived GitHub repos (default: false)

  AWS_REGION_CODECOMMIT   default us-west-2
  AWS_REGION_SES          default us-east-1
  SES_FROM                default do-not-reply@vitechinc.com
  SES_TO                  default v3atlassianops@vitechinc.com
                          (comma-separated for multiple recipients)

  ALWAYS_SEND         "true" to email even when everything is in sync
  DRY_RUN             "true" to print the report instead of emailing
"""

import os
import sys
import time
import requests
import boto3
from botocore.exceptions import ClientError

# ---------------- STATIC DEFAULTS ----------------
GITHUB_API_BASE = "https://api.github.com"
PER_PAGE = 100
REQUEST_TIMEOUT = 20
MAX_TOKEN_SLOTS = 20  # scan GITHUB_TOKEN_1 .. GITHUB_TOKEN_20


# ---------------- ENV HELPERS ----------------
def env_bool(name, default=False):
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def env_list(name):
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------- CONFIG (read once) ----------------
AWS_REGION_CODECOMMIT = os.getenv("AWS_REGION_CODECOMMIT", "us-west-2")
AWS_REGION_SES = os.getenv("AWS_REGION_SES", "us-east-1")

SES_FROM = os.getenv("SES_FROM", "do-not-reply@vitechinc.com")
SES_TO = env_list("SES_TO") or ["v3atlassianops@vitechinc.com"]

EXPLICIT_ORGS = env_list("GITHUB_ORGS")
EXCLUDED_ORGS = set(env_list("EXCLUDED_ORGS"))
EXCLUDED_REPOS = set(env_list("EXCLUDED_REPOS"))  # "repo" or "org/repo"
SKIP_ARCHIVED = env_bool("SKIP_ARCHIVED", False)

ALWAYS_SEND = env_bool("ALWAYS_SEND", False)
DRY_RUN = env_bool("DRY_RUN", False)


# ---------------- TOKEN / SESSION ----------------
def collect_tokens():
    """Collect all GITHUB_TOKEN_N (and a bare GITHUB_TOKEN) that are set."""
    tokens = []
    bare = os.getenv("GITHUB_TOKEN")
    if bare:
        tokens.append(bare)
    for i in range(1, MAX_TOKEN_SLOTS + 1):
        t = os.getenv(f"GITHUB_TOKEN_{i}")
        if t and t not in tokens:
            tokens.append(t)
    return tokens


def gh_session(token):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "v3atlassianops-sync-check",
    })
    return s


def gh_get(session, url):
    """GET with a single retry on secondary-rate-limit / 403 with reset header."""
    for attempt in range(2):
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = resp.headers.get("X-RateLimit-Reset")
            wait = 5
            if reset and reset.isdigit():
                wait = max(1, min(60, int(reset) - int(time.time()) + 1))
            print(f"[WARN] Rate limited on {url}; sleeping {wait}s")
            time.sleep(wait)
            continue
        return resp
    return resp


# ---------------- GITHUB DISCOVERY ----------------
def discover_orgs(session):
    """Orgs the token is a member of, via /user/orgs (paginated)."""
    orgs = []
    page = 1
    while True:
        url = f"{GITHUB_API_BASE}/user/orgs?per_page={PER_PAGE}&page={page}"
        resp = gh_get(session, url)
        if resp.status_code != 200:
            print(f"[WARN] /user/orgs returned {resp.status_code}: {resp.text[:150]}")
            break
        data = resp.json()
        if not data:
            break
        orgs.extend([o.get("login") for o in data if o.get("login")])
        if len(data) < PER_PAGE:
            break
        page += 1
    return orgs


def org_is_accessible(session, org):
    resp = gh_get(session, f"{GITHUB_API_BASE}/orgs/{org}")
    return resp.status_code == 200


def get_github_repos(session, org):
    """Return list of dicts: {name, default_branch, archived} for an org."""
    repos = []
    page = 1
    while True:
        url = (f"{GITHUB_API_BASE}/orgs/{org}/repos"
               f"?per_page={PER_PAGE}&page={page}&type=all")
        resp = gh_get(session, url)
        if resp.status_code != 200:
            print(f"[ERROR] repos for {org} -> {resp.status_code}: {resp.text[:150]}")
            break
        data = resp.json()
        if not data:
            break
        for r in data:
            name = r.get("name")
            if not name:
                continue
            repos.append({
                "name": name,
                "default_branch": r.get("default_branch"),
                "archived": bool(r.get("archived")),
            })
        if len(data) < PER_PAGE:
            break
        page += 1
    return repos


def get_github_commit_sha(session, org, repo, default_branch):
    """Latest commit SHA on the default branch."""
    if not default_branch:
        return "NoDefaultBranch"
    url = f"{GITHUB_API_BASE}/repos/{org}/{repo}/commits/{default_branch}"
    resp = gh_get(session, url)
    if resp.status_code == 409:
        # GitHub returns 409 for an empty repository (no commits)
        return "EmptyRepo"
    if resp.status_code != 200:
        return f"GitHubError:{resp.status_code}"
    return resp.json().get("sha", "UnknownSHA")


# ---------------- CODECOMMIT ----------------
def get_codecommit_sha(cc_client, cc_repo_name, branch_name):
    """Latest commit id of a SPECIFIC branch on a CodeCommit repo.

    We deliberately query the branch by name (the GitHub default branch),
    NOT CodeCommit's own defaultBranch. A `--mirror` push leaves CodeCommit's
    default branch pointing at whatever ref landed first, so comparing
    against it produces false mismatches. Comparing the same branch name on
    both sides is apples-to-apples.

    Returns (cc_branch_name, commitId_or_status).
    """
    if not branch_name:
        return None, "NoGitHubBranch"
    try:
        # Confirm the repo exists first (gives a clean NotFound status)
        cc_client.get_repository(repositoryName=cc_repo_name)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "RepositoryDoesNotExistException":
            return None, "NotFound"
        return None, f"CCError:{code or str(e)}"

    try:
        br = cc_client.get_branch(
            repositoryName=cc_repo_name, branchName=branch_name
        )
        return branch_name, br.get("branch", {}).get("commitId", "UnknownCommitId")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "BranchDoesNotExistException":
            return branch_name, "BranchNotFound"
        return branch_name, f"CCError:{code or str(e)}"


# ---------------- COMPARISON ----------------
def is_excluded_repo(org, repo):
    return repo in EXCLUDED_REPOS or f"{org}/{repo}" in EXCLUDED_REPOS


def compare_org(session, cc_client, org):
    """Return (rows, checked_count) where rows are mismatches only.

    Each row: [repo, default_branch, gh_sha, cc_sha, status]
    """
    rows = []
    checked = 0
    repos = get_github_repos(session, org)
    for r in repos:
        repo = r["name"]
        if is_excluded_repo(org, repo):
            continue
        if SKIP_ARCHIVED and r["archived"]:
            continue

        checked += 1
        gh_branch = r["default_branch"]
        gh_sha = get_github_commit_sha(session, org, repo, gh_branch)
        cc_repo_name = f"{org}_{repo}"
        # Compare the SAME branch name on CodeCommit (GitHub's default branch),
        # not CodeCommit's own defaultBranch.
        cc_branch, cc_sha = get_codecommit_sha(cc_client, cc_repo_name, gh_branch)

        # Exact match on the same branch -> in sync
        if gh_sha == cc_sha:
            continue

        # Both sides empty (no commits) -> treat as in sync, ignore
        if gh_sha == "EmptyRepo" and cc_sha in ("BranchNotFound", "NotFound"):
            continue

        # Classify the reason for the report
        if cc_sha == "NotFound":
            status = "Missing in CodeCommit"
        elif cc_sha == "BranchNotFound":
            status = f"Branch '{gh_branch}' not in CodeCommit"
        elif gh_sha == "EmptyRepo":
            status = "GitHub repo empty"
        elif (str(gh_sha).startswith("GitHubError")
              or str(cc_sha).startswith("CCError")
              or gh_sha in ("NoDefaultBranch", "UnknownSHA")):
            status = "Lookup error"
        else:
            status = "Commit mismatch"

        rows.append([repo, gh_branch or "-", gh_sha, cc_sha, status])

    return rows, checked


# ---------------- HTML REPORT ----------------
def short_sha(value):
    """Shorten a 40-char hex SHA to 7 chars; leave status strings intact."""
    if isinstance(value, str) and len(value) >= 40 and all(
        c in "0123456789abcdef" for c in value[:40].lower()
    ):
        return value[:7]
    return value or "-"


def build_org_table(org, rows):
    html = f"<h3>{org}</h3>"
    if not rows:
        return html + "<p>No mismatches found &#9989;</p>"
    html += (
        "<table>"
        "<tr><th>Repository</th><th>Branch Compared</th>"
        "<th>GitHub Commit</th><th>CodeCommit Commit</th><th>Status</th></tr>"
    )
    for repo, branch, gh, cc, status in rows:
        cc_repo = f"{org}_{repo}"
        html += (
            f"<tr><td>{repo}<br><span class='cc'>cc: {cc_repo}</span></td>"
            f"<td>{branch}</td>"
            f"<td title='{gh}'>{short_sha(gh)}</td>"
            f"<td title='{cc}'>{short_sha(cc)}</td>"
            f"<td>{status}</td></tr>"
        )
    html += "</table><br>"
    return html


def build_html_body(per_org_rows, summary):
    style = """
    <style>
      body { font-family: Arial, sans-serif; background:#f9f9f9; color:#333; padding:20px; }
      table { border-collapse:collapse; width:100%; background:#fff; margin:8px 0 4px; }
      th, td { border:1px solid #ccc; padding:8px; text-align:left; font-size:13px; }
      th { background:#0073e6; color:#fff; }
      tr:nth-child(even) { background:#f2f2f2; }
      h2 { color:#0073e6; }
      h3 { color:#0a3d62; margin-top:22px; border-bottom:2px solid #0073e6; padding-bottom:4px; }
      .cc { color:#888; font-size:11px; }
      .summary { background:#fff; border:1px solid #ddd; padding:12px; border-radius:6px; }
    </style>
    """
    header = (
        "<h2>Majesco GitHub vs CodeCommit Sync Report</h2>"
        f"<p>Generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}</p>"
    )

    insync = ", ".join(summary["in_sync_orgs"]) or "none"
    summary_html = (
        "<div class='summary'>"
        f"<p><b>Orgs scanned:</b> {summary['orgs_scanned']} &nbsp; "
        f"<b>Repos checked:</b> {summary['repos_checked']} &nbsp; "
        f"<b>Mismatches:</b> {summary['mismatch_count']}</p>"
        f"<p><b>Orgs fully in sync:</b> {insync}</p>"
        "</div>"
    )

    body = ""
    for org in sorted(per_org_rows.keys()):
        rows = per_org_rows[org]
        if rows:  # only show orgs that actually have mismatches
            body += build_org_table(org, rows)

    if not body:
        body = "<p>All repositories are in sync &#9989;</p>"

    footer = (
        "<p style='font-size:12px;color:#888;'>"
        "Sent automatically via GitHub Actions using AWS SES</p>"
    )
    return f"<html><head>{style}</head><body>{header}{summary_html}{body}{footer}</body></html>"


# ---------------- SES ----------------
def send_email_ses(subject, html_body):
    ses = boto3.client("ses", region_name=AWS_REGION_SES)
    try:
        response = ses.send_email(
            Source=SES_FROM,
            Destination={"ToAddresses": SES_TO},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Html": {"Data": html_body}},
            },
        )
        print("Email sent via SES:", response["MessageId"])
    except Exception as e:
        print("Failed to send email via SES:", str(e))
        sys.exit(1)


# ---------------- MAIN ----------------
def main():
    tokens = collect_tokens()
    if not tokens:
        print("Missing GitHub token(s). Set GITHUB_TOKEN_1 / GITHUB_TOKEN_2 ...")
        sys.exit(1)

    sessions = [gh_session(t) for t in tokens]

    # 1) Determine the org list
    if EXPLICIT_ORGS:
        candidate_orgs = list(dict.fromkeys(EXPLICIT_ORGS))  # de-dupe, keep order
        print(f"Using explicit org list: {candidate_orgs}")
    else:
        discovered = []
        for s in sessions:
            for org in discover_orgs(s):
                if org not in discovered:
                    discovered.append(org)
        candidate_orgs = discovered
        print(f"Discovered orgs: {candidate_orgs}")

    # Apply org exclusions
    target_orgs = [o for o in candidate_orgs if o not in EXCLUDED_ORGS]
    if EXCLUDED_ORGS:
        print(f"Excluding orgs: {sorted(EXCLUDED_ORGS)}")
    if not target_orgs:
        print("No orgs to process after exclusions. Exiting.")
        return

    # 2) Map each org to a token that can read it
    org_sessions = {}
    for org in target_orgs:
        chosen = next((s for s in sessions if org_is_accessible(s, org)), None)
        if chosen is None:
            print(f"[WARN] No token can access org '{org}'. Skipping.")
            continue
        org_sessions[org] = chosen

    if not org_sessions:
        print("No accessible orgs. Exiting.")
        return

    # 3) Compare per org
    cc_client = boto3.client("codecommit", region_name=AWS_REGION_CODECOMMIT)
    per_org_rows = {}
    repos_checked = 0
    in_sync_orgs = []

    for org, session in org_sessions.items():
        print(f"Checking org: {org} ...")
        rows, checked = compare_org(session, cc_client, org)
        per_org_rows[org] = rows
        repos_checked += checked
        if not rows:
            in_sync_orgs.append(org)
        print(f"  {checked} repos checked, {len(rows)} mismatch(es)")

    mismatch_count = sum(len(r) for r in per_org_rows.values())
    summary = {
        "orgs_scanned": len(org_sessions),
        "repos_checked": repos_checked,
        "mismatch_count": mismatch_count,
        "in_sync_orgs": sorted(in_sync_orgs),
    }

    html_body = build_html_body(per_org_rows, summary)
    subject = (
        f"Majesco GitHub Backup: {mismatch_count} sync mismatch(es) across "
        f"{len(org_sessions)} org(s)"
        if mismatch_count else
        "Majesco GitHub Backup: All repositories in sync"
    )

    if DRY_RUN:
        print("----- DRY RUN -----")
        print(subject)
        print(html_body)
        print("-------------------")
        return

    if mismatch_count == 0 and not ALWAYS_SEND:
        print("Everything in sync; no email sent (set ALWAYS_SEND=true to override).")
        return

    send_email_ses(subject, html_body)


if __name__ == "__main__":
    main()
