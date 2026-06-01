#!/usr/bin/env python3
"""
github_codecommit_backup.py

Every 4-hour run logic:
  ┌─ For each repo discovered via GitHub PAT ──────────────────────────────┐
  │                                                                         │
  │  Does  base_dir/<org>/<repo>.git  exist on disk?                       │
  │                                                                         │
  │  NO  (first time ever seeing this repo)                                 │
  │    → git clone --mirror  (creates <repo>.git automatically)            │
  │    → aws codecommit create-repository   ← only called once, ever      │
  │    → incremental push via incremental-repo-migration.py                │
  │                                                                         │
  │  YES (seen before — runs every 4 hours)                                 │
  │    → git -C <repo>.git remote update --prune                           │
  │    → git push --mirror  (only sends delta objects)                     │
  │                                                                         │
  │  rc==0 always means success. No RepositoryNameExistsException ever.    │
  └─────────────────────────────────────────────────────────────────────────┘

NOTE on bare mirror clone:
  git clone --mirror creates:   base_dir/<org>/<repo>.git/
  NOT:                          base_dir/<org>/<repo>/.git
  So the existence check is (base_dir / org / f"{repo}.git").is_dir()

Discovery is done entirely in Python (paginated GitHub API).
No shell script needed — the shell script was only for Ansible's benefit.

Environment variables:
  GITHUB_TOKEN           Classic PAT (repo + read:org scopes)
  GITHUB_USERNAME        GitHub username used in clone URLs
  CODECOMMIT_USERNAME    AWS CodeCommit HTTPS username
  CODECOMMIT_PASSWORD    AWS CodeCommit HTTPS password
  AWS_REGION             default: us-west-2
  SNS_TOPIC_ARN          SNS topic ARN for new-repo notifications
  SNS_REGION             default: us-east-1
  BASE_DIR               default: /u02/github_to_codecommit
  LOG_DIR                default: BASE_DIR/logs
  INCREMENTAL_SCRIPT     path to incremental-repo-migration.py
"""

import os, sys, json, time, shutil, logging, argparse, subprocess, threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.error

# ── logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────
GH_PER_PAGE   = 100
MAX_RETRIES   = 3
RETRY_BACKOFF = 2    # seconds, doubled each attempt
CLONE_WORKERS = 4    # concurrent git clone / remote-update threads
PUSH_WORKERS  = 3    # concurrent CodeCommit push threads

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def gh_get(path: str, token: str):
    """Single authenticated GitHub API GET with retry. Returns parsed JSON."""
    url = f"https://api.github.com{path}"
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 403 and "rate limit" in body.lower():
                wait = 60 * attempt
                log.warning("Rate limited — sleeping %ds (attempt %d/%d)", wait, attempt, MAX_RETRIES)
                time.sleep(wait)
            elif attempt == MAX_RETRIES:
                log.error("GitHub API %s → HTTP %d: %s", url, e.code, body)
                raise
            else:
                time.sleep(RETRY_BACKOFF ** attempt)
        except Exception:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF ** attempt)


def gh_paginate(path_tpl: str, token: str) -> list:
    """Collect all pages from a GitHub list endpoint."""
    results, page = [], 1
    while True:
        data = gh_get(f"{path_tpl}&page={page}", token)
        if not data:
            break
        results.extend(data)
        if len(data) < GH_PER_PAGE:
            break
        page += 1
    return results


def run_cmd(cmd: list, cwd=None, env=None, timeout=1800) -> tuple:
    """Run a subprocess. Returns (rc, stdout, stderr)."""
    merged = {**os.environ, **(env or {})}
    try:
        p = subprocess.run(
            cmd, cwd=cwd, env=merged,
            capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as exc:
        return 1, "", str(exc)


def mask(text: str, *secrets: str) -> str:
    """Strip credentials from text before writing to disk."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def append_jsonl(path: Path, record: dict, lock: threading.Lock):
    """Thread-safe append of one JSON record to a .jsonl log file."""
    line = json.dumps(record) + "\n"
    with lock:
        with open(path, "a") as f:
            f.write(line)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Discovery  (pure Python, no shell script needed)
# ─────────────────────────────────────────────────────────────────────────────

def discover(token: str) -> tuple:
    """
    Returns (orgs: list[str], repos: list[dict{org, repo}])
    Entirely in Python — no shell script required.
    """
    log.info("── Phase 1: Discovery ──")

    raw_orgs = gh_paginate(f"/user/orgs?per_page={GH_PER_PAGE}", token)
    orgs     = [o["login"] for o in raw_orgs]
    log.info("Found %d organizations: %s", len(orgs), orgs)

    repos = []
    for org in orgs:
        raw  = gh_paginate(
            f"/orgs/{org}/repos?per_page={GH_PER_PAGE}&type=all", token
        )
        mine = [{"org": org, "repo": r["name"]} for r in raw]
        repos.extend(mine)
        log.info("  %-40s %d repos", org, len(mine))

    log.info("Total repositories discovered: %d", len(repos))
    return orgs, repos


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Classify + Clone/Update
#
# KEY POINT — bare mirror clone directory structure:
#   git clone --mirror https://.../org/repo
#   creates → base_dir/org/repo.git/          ← bare repo, no .git subdir
#
# So the existence check is:
#   (base_dir / org / f"{repo}.git").is_dir()
#
# NOT:
#   (base_dir / org / repo / ".git").is_dir()   ← WRONG for mirror clones
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_bare_repo(repo_dir: Path) -> bool:
    """
    Returns True only if repo_dir is a structurally complete bare git repo.
    Checks for the three things git itself requires:
      - HEAD file exists and is non-empty
      - objects/ directory exists
      - refs/ directory exists
    A half-finished clone will be missing at least one of these.
    """
    return (
        (repo_dir / "HEAD").is_file()
        and (repo_dir / "HEAD").stat().st_size > 0
        and (repo_dir / "objects").is_dir()
        and (repo_dir / "refs").is_dir()
    )


def cc_repo_exists(cc_name: str, region: str) -> bool:
    """
    Returns True if the CodeCommit repo already exists.
    Uses describe-repository — a lightweight read-only call.
    """
    rc, _, _ = run_cmd([
        "aws", "codecommit", "get-repository",
        "--repository-name", cc_name,
        "--region", region,
    ])
    return rc == 0


def clone_or_update_one(item: dict, base_dir: Path,
                        gh_user: str, gh_token: str,
                        region: str) -> dict:
    org      = item["org"]
    repo     = item["repo"]
    org_dir  = base_dir / org
    repo_dir = org_dir / f"{repo}.git"
    cc_name  = f"{org}_{repo}"

    org_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "org":         org,
        "repo":        repo,
        "is_new":      False,
        "clone_rc":    None,
        "clone_error": "",
    }

    # ── Classify: existing only if BOTH conditions are true ──────────────────
    #
    #   1. repo.git/ is a structurally valid bare repo  (not a partial clone)
    #   2. The CodeCommit repo already exists           (not skipped last run)
    #
    # If either condition fails we treat it as NEW:
    #   - Partial clone  → wipe repo.git/, re-clone cleanly
    #   - Valid clone but no CC repo → re-use local clone, create CC + push
    #
    local_valid = repo_dir.is_dir() and is_valid_bare_repo(repo_dir)
    cc_exists   = local_valid and cc_repo_exists(cc_name, region)

    if local_valid and cc_exists:
        # ── EXISTING: both sides healthy — just sync ─────────────────────────
        result["is_new"] = False
        for attempt in range(1, MAX_RETRIES + 1):
            rc, _, err = run_cmd(
                ["git", "-C", str(repo_dir), "remote", "update", "--prune"],
                timeout=600,
            )
            result["clone_rc"]    = rc
            result["clone_error"] = "" if rc == 0 else mask(err, gh_token, gh_user)
            if rc == 0:
                break
            if attempt < MAX_RETRIES:
                log.warning("  [%s/%s] update attempt %d failed — retrying in %ds",
                            org, repo, attempt, RETRY_BACKOFF ** attempt)
                time.sleep(RETRY_BACKOFF ** attempt)

    else:
        # ── NEW (or broken): clone from scratch ──────────────────────────────
        result["is_new"] = True

        if repo_dir.is_dir() and not local_valid:
            # Partial/corrupt clone left over from a previous failed run —
            # remove it so git clone starts with a clean slate.
            log.warning("  [%s/%s] Incomplete clone detected — removing %s and re-cloning",
                        org, repo, repo_dir)
            shutil.rmtree(repo_dir)
        elif repo_dir.is_dir() and local_valid and not cc_exists:
            # Valid local clone but CodeCommit repo is missing.
            # Re-use the local clone (no need to re-download) — just re-create
            # the CC repo and push. Mark clone as already done.
            log.info("  [%s/%s] Local clone OK but CodeCommit repo missing — will create + push",
                     org, repo)
            result["clone_rc"] = 0   # local clone is fine, nothing to re-clone
            return result

        clone_url = (
            f"https://{gh_user}:{gh_token}"
            f"@github.com/{org}/{repo}"
        )
        for attempt in range(1, MAX_RETRIES + 1):
            rc, _, err = run_cmd(
                ["git", "clone", "--mirror", clone_url],
                cwd=str(org_dir),
                timeout=1800,
            )
            result["clone_rc"]    = rc
            result["clone_error"] = "" if rc == 0 else mask(err, gh_token, gh_user)
            if rc == 0:
                break
            if attempt < MAX_RETRIES:
                log.warning("  [%s/%s] clone attempt %d failed — retrying in %ds",
                            org, repo, attempt, RETRY_BACKOFF ** attempt)
                time.sleep(RETRY_BACKOFF ** attempt)

    return result


def phase_clone(repos: list, base_dir: Path,
                gh_user: str, gh_token: str,
                region: str, log_dir: Path) -> tuple:
    """
    Classifies every repo by checking BOTH local disk state AND CodeCommit
    existence. Handles four cases:
      1. repo.git valid  + CC exists  → existing  (update + mirror push)
      2. repo.git valid  + CC missing → new        (re-use clone, create CC + push)
      3. repo.git broken + CC missing → new        (wipe, re-clone, create CC + push)
      4. repo.git absent              → new        (clone, create CC + push)

    Returns (new_repos, existing_repos).
    """
    log.info("── Phase 2: Clone / update %d repos (workers=%d) ──",
             len(repos), CLONE_WORKERS)

    new_repos, existing_repos = [], []
    clone_log  = log_dir / "clone_results.jsonl"
    file_lock  = threading.Lock()

    with ThreadPoolExecutor(max_workers=CLONE_WORKERS) as pool:
        futures = {
            pool.submit(
                clone_or_update_one, item, base_dir, gh_user, gh_token, region
            ): item
            for item in repos
        }
        done = 0
        for future in as_completed(futures):
            res    = future.result()
            done  += 1
            tag    = "NEW     " if res["is_new"] else "existing"
            status = "ok" if res["clone_rc"] == 0 else "FAILED"

            log.info("  [%d/%d] %-45s [%s] %s",
                     done, len(repos),
                     f"{res['org']}/{res['repo']}", tag, status)

            append_jsonl(clone_log, res, file_lock)

            if res["clone_rc"] == 0:
                entry = {"org": res["org"], "repo": res["repo"]}
                if res["is_new"]:
                    new_repos.append(entry)
                else:
                    existing_repos.append(entry)

    failed = len(repos) - len(new_repos) - len(existing_repos)
    log.info("Clone phase: new=%d  existing=%d  failed=%d",
             len(new_repos), len(existing_repos), failed)
    return new_repos, existing_repos


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Create CodeCommit repos  (NEW repos only, called once ever)
# ─────────────────────────────────────────────────────────────────────────────

def create_cc_repo(cc_name: str, region: str) -> tuple:
    """Returns (success: bool, stderr: str)"""
    rc, _, err = run_cmd([
        "aws", "codecommit", "create-repository",
        "--repository-name", cc_name,
        "--region", region,
    ])
    return rc == 0, err


def phase_create(new_repos: list, region: str, log_dir: Path) -> tuple:
    """
    Creates a CodeCommit repo for each newly cloned repo.
    rc==0 → success, proceed to incremental push.
    rc!=0 → genuine AWS error, log and skip.
    RepositoryNameExistsException never appears here because we only call
    create for repos whose .git dir didn't exist locally before this run.
    """
    log.info("── Phase 3: Create CodeCommit repos for %d new repos ──",
             len(new_repos))

    ready, failed = [], []
    create_log = log_dir / "codecommit_create_results.jsonl"
    file_lock  = threading.Lock()

    for item in new_repos:
        cc_name = f"{item['org']}_{item['repo']}"
        ok, err = create_cc_repo(cc_name, region)

        append_jsonl(create_log, {
            "cc_name": cc_name, "success": ok, "error": err
        }, file_lock)

        if ok:
            log.info("  Created: %s", cc_name)
            ready.append(item)
        else:
            log.error("  FAILED to create %s — %s", cc_name, err)
            failed.append({"item": item, "error": err})

    log.info("Create phase: created=%d  failed=%d", len(ready), len(failed))
    return ready, failed


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Push
#   NEW repos      → incremental push (handles large first pushes safely)
#   EXISTING repos → git push --mirror (only sends delta objects)
# ─────────────────────────────────────────────────────────────────────────────

def _cc_url(cc_user: str, cc_pass: str, region: str, cc_name: str) -> str:
    return (
        f"https://{cc_user}:{cc_pass}"
        f"@git-codecommit.{region}.amazonaws.com/v1/repos/{cc_name}"
    )


def push_incremental_one(item: dict, base_dir: Path,
                         cc_user: str, cc_pass: str,
                         region: str, inc_script: str) -> dict:
    """Incremental push for a brand-new CodeCommit repo."""
    org, repo = item["org"], item["repo"]
    cc_name   = f"{org}_{repo}"
    # bare mirror clone lives at <org>/<repo>.git  (no .git subdir inside)
    repo_dir  = base_dir / org / f"{repo}.git"
    result    = {"org": org, "repo": repo, "push_type": "incremental",
                 "push_rc": None, "push_error": ""}

    dest_script = repo_dir / "incremental-repo-migration.py"
    try:
        shutil.copy2(inc_script, dest_script)
    except Exception as exc:
        result["push_rc"]    = 1
        result["push_error"] = str(exc)
        return result

    url = _cc_url(cc_user, cc_pass, region, cc_name)

    for attempt in range(1, MAX_RETRIES + 1):
        # Clean up any leftover remote from a previous failed attempt
        run_cmd(["git", "remote", "remove", "codecommit"], cwd=str(repo_dir))
        run_cmd(["git", "remote", "add",    "codecommit", url], cwd=str(repo_dir))

        rc, _, err = run_cmd(
            ["python3", "incremental-repo-migration.py"],
            cwd=str(repo_dir),
            timeout=3600,
        )
        run_cmd(["git", "remote", "remove", "codecommit"], cwd=str(repo_dir))

        result["push_rc"] = rc
        if rc == 0:
            break
        result["push_error"] = mask(err, cc_pass, cc_user)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF ** attempt)

    return result


def push_mirror_one(item: dict, base_dir: Path,
                    cc_user: str, cc_pass: str, region: str) -> dict:
    """Mirror push for an existing CodeCommit repo (delta only)."""
    org, repo = item["org"], item["repo"]
    cc_name   = f"{org}_{repo}"
    repo_dir  = base_dir / org / f"{repo}.git"
    url       = _cc_url(cc_user, cc_pass, region, cc_name)
    result    = {"org": org, "repo": repo, "push_type": "mirror",
                 "push_rc": None, "push_error": ""}

    for attempt in range(1, MAX_RETRIES + 1):
        run_cmd(["git", "fetch", "--prune"], cwd=str(repo_dir))
        rc, _, err = run_cmd(
            ["git", "push", "--mirror", url],
            cwd=str(repo_dir),
            timeout=3600,
        )
        result["push_rc"] = rc
        if rc == 0:
            break
        result["push_error"] = mask(err, cc_pass, cc_user)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF ** attempt)

    return result


def phase_push(new_repos: list, existing_repos: list,
               base_dir: Path, cc_user: str, cc_pass: str,
               region: str, inc_script: str, log_dir: Path) -> tuple:

    push_log  = log_dir / "push_results.jsonl"
    file_lock = threading.Lock()
    inc_results, mirror_results = [], []

    # ── 4a: incremental push for NEW repos ──────────────────────────────────
    log.info("── Phase 4a: Incremental push for %d new repos (workers=%d) ──",
             len(new_repos), PUSH_WORKERS)

    with ThreadPoolExecutor(max_workers=PUSH_WORKERS) as pool:
        futures = {
            pool.submit(
                push_incremental_one, item, base_dir,
                cc_user, cc_pass, region, inc_script
            ): item
            for item in new_repos
        }
        done = 0
        for future in as_completed(futures):
            res    = future.result()
            done  += 1
            status = "ok" if res["push_rc"] == 0 else "FAILED"
            log.info("  [%d/%d] %s/%s — incremental push %s",
                     done, len(new_repos), res["org"], res["repo"], status)
            append_jsonl(push_log, res, file_lock)
            inc_results.append(res)

    # ── 4b: mirror push for EXISTING repos ──────────────────────────────────
    log.info("── Phase 4b: Mirror push for %d existing repos (workers=%d) ──",
             len(existing_repos), PUSH_WORKERS)

    with ThreadPoolExecutor(max_workers=PUSH_WORKERS) as pool:
        futures = {
            pool.submit(
                push_mirror_one, item, base_dir,
                cc_user, cc_pass, region
            ): item
            for item in existing_repos
        }
        done = 0
        for future in as_completed(futures):
            res    = future.result()
            done  += 1
            status = "ok" if res["push_rc"] == 0 else "FAILED"
            log.info("  [%d/%d] %s/%s — mirror push %s",
                     done, len(existing_repos), res["org"], res["repo"], status)
            append_jsonl(push_log, res, file_lock)
            mirror_results.append(res)

    return inc_results, mirror_results


# ─────────────────────────────────────────────────────────────────────────────
# SNS
# ─────────────────────────────────────────────────────────────────────────────

def sns_notify(topic_arn: str, region: str, subject: str, message: str):
    rc, _, err = run_cmd([
        "aws", "sns", "publish",
        "--topic-arn", topic_arn,
        "--region",    region,
        "--subject",   subject,
        "--message",   message,
    ])
    if rc != 0:
        log.warning("SNS publish failed: %s", err)
    else:
        log.info("SNS notification sent.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GitHub → CodeCommit backup")
    parser.add_argument("--dry-run", action="store_true",
                        help="Discovery + report only — no clone/create/push")
    parser.add_argument("--workers", type=int, default=None,
                        help="Override CLONE_WORKERS (PUSH_WORKERS = workers//2)")
    args = parser.parse_args()

    if args.workers:
        global CLONE_WORKERS, PUSH_WORKERS
        CLONE_WORKERS = args.workers
        PUSH_WORKERS  = max(1, args.workers // 2)

    def env(name: str, default: str = "") -> str:
        val = os.environ.get(name, default)
        if not val and not args.dry_run and name not in ("SNS_TOPIC_ARN",):
            log.error("Required environment variable not set: %s", name)
            sys.exit(1)
        return val

    gh_token   = env("GITHUB_TOKEN")
    gh_user    = env("GITHUB_USERNAME")
    cc_user    = env("CODECOMMIT_USERNAME")
    cc_pass    = env("CODECOMMIT_PASSWORD")
    region     = env("AWS_REGION",    "us-west-2")
    sns_arn    = env("SNS_TOPIC_ARN", "")
    sns_region = env("SNS_REGION",    "us-east-1")
    base_dir   = Path(env("BASE_DIR", "/u02/github_to_codecommit"))
    log_dir    = Path(env("LOG_DIR",  str(base_dir / "logs")))
    inc_script = env("INCREMENTAL_SCRIPT",
                     str(Path(__file__).parent / "incremental-repo-migration.py"))

    base_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Tee all log output to disk as well
    fh = logging.FileHandler(log_dir / "backup.log")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(fh)

    started = datetime.now(timezone.utc).isoformat()
    log.info("══ GitHub → CodeCommit backup  started=%s  mode=%s ══",
             started, "DRY-RUN" if args.dry_run else "FULL")
    log.info("Base dir : %s", base_dir)
    log.info("Log dir  : %s", log_dir)
    log.info("Region   : %s", region)

    # ── Phase 1: discovery (pure Python, no shell script) ───────────────────
    orgs, repos = discover(gh_token)

    if args.dry_run:
        report = {
            "mode":    "dry_run",
            "orgs":    orgs,
            "per_org": {o: sum(1 for r in repos if r["org"] == o) for o in orgs},
            "total":   len(repos),
        }
        write_json(log_dir / "dry_run_report.json", report)
        log.info("DRY-RUN complete. Report written to %s/dry_run_report.json", log_dir)
        sys.exit(0)

    # ── Phase 2: classify by disk + clone/update ─────────────────────────────
    new_repos, existing_repos = phase_clone(
        repos, base_dir, gh_user, gh_token, region, log_dir
    )

    # ── Phase 3: create CodeCommit repos (new only) ──────────────────────────
    push_ready_new, failed_creates = phase_create(new_repos, region, log_dir)

    # ── Phase 4: push ─────────────────────────────────────────────────────────
    inc_results, mirror_results = phase_push(
        push_ready_new, existing_repos,
        base_dir, cc_user, cc_pass, region, inc_script, log_dir,
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    new_names     = [f"{r['org']}_{r['repo']}" for r in push_ready_new]
    push_failures = [r for r in inc_results + mirror_results if r["push_rc"] != 0]

    summary = {
        "started_at":       started,
        "finished_at":      datetime.now(timezone.utc).isoformat(),
        "orgs":             len(orgs),
        "total_repos":      len(repos),
        "new_repos":        new_names,
        "new_repos_count":  len(new_names),
        "existing_count":   len(existing_repos),
        "failed_creates":   [
            f"{i['item']['org']}_{i['item']['repo']}" for i in failed_creates
        ],
        "push_failures":    [
            f"{r['org']}_{r['repo']}" for r in push_failures
        ],
    }
    write_json(log_dir / "summary.json", summary)

    log.info("══ Summary ══")
    log.info("  orgs              : %d", summary["orgs"])
    log.info("  total repos       : %d", summary["total_repos"])
    log.info("  new repos         : %d", summary["new_repos_count"])
    log.info("  existing repos    : %d", summary["existing_count"])
    log.info("  failed creates    : %d", len(failed_creates))
    log.info("  push failures     : %d", len(push_failures))

    # ── SNS: only when new repos were added this run ─────────────────────────
    if sns_arn and new_names:
        msg = (
            "New repositories added to AWS CodeCommit:\n\n"
            + "\n".join(new_names)
            + f"\n\nTotal new: {len(new_names)}\n\nThanks,\nv3atlassianops"
        )
        sns_notify(sns_arn, sns_region,
                   "New Repositories Added To AWS CodeCommit", msg)

    log.info("══ Backup finished at %s ══",
             datetime.now(timezone.utc).isoformat())

    # Non-zero exit so CI/GitHub Actions marks the run as failed if errors exist
    if failed_creates or push_failures:
        log.warning("Completed with errors — review %s/summary.json", log_dir)
        sys.exit(1)


if __name__ == "__main__":
    main()
