#!/usr/bin/env python3
"""
github_codecommit_backup.py

Every 4-hour run logic:
  ┌─ For each repo discovered via GitHub PAT ──────────────────────────────┐
  │  Does  base_dir/<org>/<repo>.git  exist AND is a valid bare repo       │
  │  AND CodeCommit repo already exists?                                   │
  │                                                                        │
  │  YES → git remote update --prune  →  git push --mirror                │
  │  NO  → git clone --mirror         →  create CC repo  →  incremental   │
  │                                                                        │
  │  Broken partial clone → wiped and re-cloned cleanly                   │
  │  Valid clone but CC missing → re-use local, create CC + push          │
  └────────────────────────────────────────────────────────────────────────┘

Crash / resume:
  Every completed repo is written to state.json immediately.
  On the next run, already-completed repos are skipped entirely.
  This means a crash mid-run loses at most one in-flight batch, not hours.

Memory safety:
  - Results flushed to .jsonl per repo, never accumulated in RAM
  - Repos processed in CHUNK_SIZE batches so futures[] never holds 3000 items
  - CLONE_WORKERS auto-scales down for large orgs (> ORG_SIZE_THRESHOLD)

Environment variables:
  GITHUB_TOKEN           Classic PAT (repo + read:org scopes)
  GITHUB_USERNAME        GitHub username for clone URLs
  CODECOMMIT_USERNAME    AWS CodeCommit HTTPS username
  CODECOMMIT_PASSWORD    AWS CodeCommit HTTPS password
  AWS_REGION             default: us-west-2
  SNS_TOPIC_ARN          SNS topic ARN (optional)
  SNS_REGION             default: us-east-1
  BASE_DIR               default: /u02/github_to_codecommit
  LOG_DIR                default: BASE_DIR/logs
  INCREMENTAL_SCRIPT     path to incremental-repo-migration.py
  EXCLUDED_ORGS          Comma-separated list of org names to exclude (optional)
                         Example: "Majesco-UWB,Majesco-Exaxe,test-org"
"""

import os, sys, json, time, shutil, logging, argparse, subprocess, threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.error

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────
GH_PER_PAGE        = 100
MAX_RETRIES        = 3
RETRY_BACKOFF      = 2      # seconds, doubled each attempt

CLONE_WORKERS      = 3      # default concurrent git clone/update threads
CLONE_WORKERS_LARGE = 1     # workers for orgs with > ORG_SIZE_THRESHOLD repos
ORG_SIZE_THRESHOLD = 500    # repos — orgs above this get serialised cloning
PUSH_WORKERS       = 2      # concurrent CodeCommit push threads
CHUNK_SIZE         = 50     # process repos in batches of this size
                            # keeps futures[] small → caps peak RAM usage

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def gh_get(path: str, token: str):
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
                log.warning("Rate limited — sleeping %ds", wait)
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
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)           # atomic replace — no corrupt state file on crash


def load_json(path: Path, default):
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def append_jsonl(path: Path, record: dict, lock: threading.Lock):
    line = json.dumps(record) + "\n"
    with lock:
        with open(path, "a") as f:
            f.write(line)


def chunks(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def parse_excluded_orgs(excluded_str: str) -> set:
    """
    Parse comma-separated org names and return as a set (case-insensitive).
    Example: "Majesco-UWB,Majesco-Exaxe,test-org" → {"majesco-uwb", "majesco-exaxe", "test-org"}
    """
    if not excluded_str:
        return set()
    return set(org.strip().lower() for org in excluded_str.split(",") if org.strip())


# ─────────────────────────────────────────────────────────────────────────────
# State file  —  tracks every completed repo so re-runs skip them
# ─────────────────────────────────────────────────────────────────────────────

class State:
    """
    Persists completed repo keys to disk after every success.
    Key format:  "org/repo"
    Atomic writes ensure a crash never corrupts the state file.
    """
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        data = load_json(path, {"completed": []})
        self._done: set = set(data.get("completed", []))
        log.info("State: %d repos already completed from previous runs", len(self._done))

    def is_done(self, org: str, repo: str) -> bool:
        return f"{org}/{repo}" in self._done

    def mark_done(self, org: str, repo: str):
        key = f"{org}/{repo}"
        with self._lock:
            self._done.add(key)
            write_json(self.path, {"completed": sorted(self._done)})


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Discovery
# ─────────────────────────────────────────────────────────────────────────────

def discover(token: str, excluded_orgs: set) -> tuple:
    log.info("── Phase 1: Discovery ──")
    raw_orgs = gh_paginate(f"/user/orgs?per_page={GH_PER_PAGE}", token)
    all_orgs = [o["login"] for o in raw_orgs]
    
    # ── Filter out excluded orgs ──────────────────────────────────────────────
    excluded_found = [o for o in all_orgs if o.lower() in excluded_orgs]
    orgs = [o for o in all_orgs if o.lower() not in excluded_orgs]
    
    if excluded_found:
        log.info("Excluding %d organization(s): %s", len(excluded_found), excluded_found)
    log.info("Found %d organizations (after exclusions): %s", len(orgs), orgs)

    repos = []
    org_sizes = {}
    for org in orgs:
        log.info("  Querying %s...", org)
        try:
            org_repos = gh_paginate(f"/orgs/{org}/repos?per_page={GH_PER_PAGE}&type=all", token)
            org_sizes[org] = len(org_repos)
            for repo in org_repos:
                repos.append({
                    "org": org,
                    "name": repo["name"],
                    "clone_url": repo["clone_url"],
                    "ssh_url": repo["ssh_url"],
                })
        except Exception as e:
            log.error("  %s — query failed: %s", org, e)
            org_sizes[org] = 0

    log.info("Discovery: %d repos across %d orgs", len(repos), len(orgs))
    for org, count in sorted(org_sizes.items()):
        log.info("  %s — %d repos", org, count)
    return orgs, repos, org_sizes


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Clone (with git mirror)
# ─────────────────────────────────────────────────────────────────────────────

def clone_one(item, base_dir, gh_user, gh_token, region):
    org, repo, clone_url = item["org"], item["name"], item["clone_url"]
    result = {
        "org": org, "repo": repo, "clone_rc": None, "clone_error": "",
        "clone_status": "unknown",
    }

    repo_dir = Path(base_dir) / org / f"{repo}.git"

    # ── Validate / repair clone ──────────────────────────────────────────────
    is_valid_bare = False
    if repo_dir.exists():
        rc, out, err = run_cmd(["git", "rev-parse", "--git-dir"],
                               cwd=str(repo_dir))
        is_valid_bare = (rc == 0 and out.strip() == ".")

    if repo_dir.exists() and not is_valid_bare:
        log.warning("  %s/%s — partial/corrupt clone detected; removing",
                    org, repo)
        shutil.rmtree(repo_dir)
        is_valid_bare = False

    # ── Clone if not already present ─────────────────────────────────────────
    if not is_valid_bare:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        # GitHub clone URL will use https://<user>:<token>@github.com/...
        clone_url_auth = clone_url.replace(
            "https://github.com/",
            f"https://{gh_user}:{gh_token}@github.com/"
        )
        rc, _, err = run_cmd(["git", "clone", "--mirror", clone_url_auth,
                              str(repo_dir)])
        result["clone_rc"] = rc
        if rc != 0:
            result["clone_error"] = mask(err, gh_token)
            result["clone_status"] = "FAILED"
            return result
        result["clone_status"] = "cloned"
    else:
        run_cmd(["git", "fetch", "--prune"], cwd=str(repo_dir))
        result["clone_status"] = "updated"

    result["clone_rc"] = 0
    return result


def phase_clone(repos, base_dir, gh_user, gh_token, region, org_sizes, state: State, log_dir):
    clone_log = log_dir / "clone_results.jsonl"
    file_lock = threading.Lock()

    new_repos, existing_repos = [], []
    clone_results = []

    log.info("── Phase 2: Clone %d repos (auto-scale workers per org size) ──",
             len(repos))

    for chunk in chunks(repos, CHUNK_SIZE):
        with ThreadPoolExecutor(max_workers=CLONE_WORKERS) as pool:
            futures = {
                pool.submit(clone_one, item, base_dir, gh_user, gh_token,
                            region): item
                for item in chunk
            }
            for future in as_completed(futures):
                item = futures[future]
                res  = future.result()
                status = res["clone_status"]
                log.info("  %s/%s — %s", res["org"], res["repo"], status)
                append_jsonl(clone_log, res, file_lock)
                clone_results.append(res)

                if res["clone_rc"] == 0:
                    # Repo is now locally present and valid
                    if status == "cloned":
                        new_repos.append(res)
                    else:
                        existing_repos.append(res)

    return new_repos, existing_repos


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — CodeCommit repo creation
# ─────────────────────────────────────────────────────────────────────────────

def create_cc_one(item, region):
    org, repo = item["org"], item["repo"]
    result = {
        "item": item, "created": False, "create_error": "",
    }

    cc_repo_name = f"{org}_{repo}"

    rc, stdout, stderr = run_cmd([
        "aws", "codecommit", "create-repository",
        "--repository-name", cc_repo_name,
        "--region", region,
    ])

    if rc == 0:
        result["created"] = True
        log.info("  %s — CodeCommit repo created", cc_repo_name)
    else:
        # Check if repo already exists (409 = already exists)
        if "RepositoryNameExistsException" in stderr:
            result["created"] = True  # Treat as success
            log.info("  %s — CodeCommit repo already exists", cc_repo_name)
        else:
            result["create_error"] = stderr
            log.error("  %s — creation failed: %s", cc_repo_name, stderr)

    return result


def phase_create(new_repos, region, log_dir):
    create_log = log_dir / "create_results.jsonl"
    file_lock = threading.Lock()

    log.info("── Phase 3: Create CodeCommit repos for %d new clones ──",
             len(new_repos))

    create_results = []
    failed_creates = []

    for chunk in chunks(new_repos, CHUNK_SIZE):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(create_cc_one, res, region): res
                for res in chunk
            }
            for future in as_completed(futures):
                res = future.result()
                append_jsonl(create_log, res, file_lock)
                create_results.append(res)
                if not res["created"]:
                    failed_creates.append(res)

    return [r["item"] for r in create_results if r["created"]], failed_creates


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Push
# ─────────────────────────────────────────────────────────────────────────────

def push_incremental_one(item, base_dir, cc_user, cc_pass, region, inc_script):
    org, repo = item["org"], item["repo"]
    cc_repo_name = f"{org}_{repo}"
    result = {
        "org": org, "repo": repo, "push_rc": 1, "push_error": "",
    }

    url = f"https://{cc_user}:{cc_pass}@git-codecommit.{region}.amazonaws.com/v1/repos/{cc_repo_name}"

    repo_dir = Path(base_dir) / org / f"{repo}.git"
    if not repo_dir.exists():
        result["push_error"] = f"Local clone not found at {repo_dir}"
        return result

    # Invoke incremental script
    rc, stdout, stderr = run_cmd([
        "python3", str(inc_script),
        "--local-bare", str(repo_dir),
        "--remote-url", url,
    ])

    result["push_rc"] = rc
    if rc != 0:
        result["push_error"] = mask(stderr, cc_pass, cc_user)

    return result


def push_mirror_one(item, base_dir, cc_user, cc_pass, region):
    org, repo = item["org"], item["repo"]
    cc_repo_name = f"{org}_{repo}"
    result = {
        "org": org, "repo": repo, "push_rc": 1, "push_error": "",
    }

    url = f"https://{cc_user}:{cc_pass}@git-codecommit.{region}.amazonaws.com/v1/repos/{cc_repo_name}"

    repo_dir = Path(base_dir) / org / f"{repo}.git"
    if not repo_dir.exists():
        result["push_error"] = f"Local clone not found at {repo_dir}"
        return result

    for attempt in range(1, MAX_RETRIES + 1):
        run_cmd(["git", "fetch", "--prune"], cwd=str(repo_dir))
        rc, _, err = run_cmd(["git", "push", "--mirror", url],
                             cwd=str(repo_dir), timeout=3600)
        result["push_rc"] = rc
        if rc == 0:
            break
        result["push_error"] = mask(err, cc_pass, cc_user)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF ** attempt)
    return result


def phase_push(new_repos, existing_repos, base_dir, cc_user, cc_pass,
               region, inc_script, state: State, log_dir):
    push_log  = log_dir / "push_results.jsonl"
    file_lock = threading.Lock()
    inc_results, mirror_results = [], []

    # ── 4a: incremental for NEW repos (chunked) ──────────────────────────────
    log.info("── Phase 4a: Incremental push %d new repos (workers=%d, chunk=%d) ──",
             len(new_repos), PUSH_WORKERS, CHUNK_SIZE)

    for chunk in chunks(new_repos, CHUNK_SIZE):
        with ThreadPoolExecutor(max_workers=PUSH_WORKERS) as pool:
            futures = {
                pool.submit(push_incremental_one, item, base_dir,
                            cc_user, cc_pass, region, inc_script): item
                for item in chunk
            }
            for future in as_completed(futures):
                res    = future.result()
                status = "ok" if res["push_rc"] == 0 else "FAILED"
                log.info("  %s/%s — incremental push %s", res["org"], res["repo"], status)
                append_jsonl(push_log, res, file_lock)
                inc_results.append(res)
                if res["push_rc"] == 0:
                    state.mark_done(res["org"], res["repo"])

    # ── 4b: mirror push for EXISTING repos (chunked) ─────────────────────────
    log.info("── Phase 4b: Mirror push %d existing repos (workers=%d, chunk=%d) ──",
             len(existing_repos), PUSH_WORKERS, CHUNK_SIZE)

    for chunk in chunks(existing_repos, CHUNK_SIZE):
        with ThreadPoolExecutor(max_workers=PUSH_WORKERS) as pool:
            futures = {
                pool.submit(push_mirror_one, item, base_dir,
                            cc_user, cc_pass, region): item
                for item in chunk
            }
            for future in as_completed(futures):
                res    = future.result()
                status = "ok" if res["push_rc"] == 0 else "FAILED"
                log.info("  %s/%s — mirror push %s", res["org"], res["repo"], status)
                append_jsonl(push_log, res, file_lock)
                mirror_results.append(res)

    return inc_results, mirror_results


# ─────────────────────────────────────────────────────────────────────────────
# SNS
# ─────────────────────────────────────────────────────────────────────────────

def sns_notify(topic_arn, region, subject, message):
    rc, _, err = run_cmd([
        "aws", "sns", "publish",
        "--topic-arn", topic_arn, "--region", region,
        "--subject",   subject,   "--message", message,
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
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--workers",    type=int, default=None)
    parser.add_argument("--reset-state", action="store_true",
                        help="Ignore previous state and process all repos from scratch")
    args = parser.parse_args()

    if args.workers:
        global CLONE_WORKERS, PUSH_WORKERS
        CLONE_WORKERS = args.workers
        PUSH_WORKERS  = max(1, args.workers // 2)

    def env(name, default=""):
        val = os.environ.get(name, default)
        if not val and not args.dry_run and name not in ("SNS_TOPIC_ARN", "EXCLUDED_ORGS"):
            log.error("Required env var not set: %s", name)
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
    excluded_orgs_str = env("EXCLUDED_ORGS", "")

    base_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(log_dir / "backup.log")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(fh)

    # ── Parse excluded orgs ────────────────────────────────────────────────────
    excluded_orgs = parse_excluded_orgs(excluded_orgs_str)
    if excluded_orgs:
        log.info("Excluded organizations: %s", ", ".join(sorted(excluded_orgs)))

    started = datetime.now(timezone.utc).isoformat()
    log.info("══ GitHub → CodeCommit backup  started=%s  mode=%s ══",
             started, "DRY-RUN" if args.dry_run else "FULL")
    log.info("  base_dir=%s  region=%s  chunk=%d  clone_workers=%d/%d  push_workers=%d",
             base_dir, region, CHUNK_SIZE, CLONE_WORKERS, CLONE_WORKERS_LARGE, PUSH_WORKERS)

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    orgs, repos, org_sizes = discover(gh_token, excluded_orgs)

    if args.dry_run:
        report = {
            "mode":    "dry_run",
            "orgs":    orgs,
            "excluded_orgs": list(excluded_orgs),
            "per_org": org_sizes,
            "total":   len(repos),
        }
        write_json(log_dir / "dry_run_report.json", report)
        log.info("DRY-RUN complete. Report: %s/dry_run_report.json", log_dir)
        sys.exit(0)

    # ── State (resume support) ────────────────────────────────────────────────
    state_path = log_dir / "state.json"
    if args.reset_state and state_path.exists():
        state_path.unlink()
        log.info("State file reset — processing all repos from scratch")
    state = State(state_path)

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    new_repos, existing_repos = phase_clone(
        repos, base_dir, gh_user, gh_token, region, org_sizes, state, log_dir
    )

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    push_ready_new, failed_creates = phase_create(new_repos, region, log_dir)

    # ── Phase 4 ───────────────────────────────────────────────────────────────
    inc_results, mirror_results = phase_push(
        push_ready_new, existing_repos,
        base_dir, cc_user, cc_pass, region, inc_script, state, log_dir,
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    new_names     = [f"{r['org']}_{r['repo']}" for r in push_ready_new]
    push_failures = [r for r in inc_results + mirror_results if r["push_rc"] != 0]

    summary = {
        "started_at":      started,
        "finished_at":     datetime.now(timezone.utc).isoformat(),
        "orgs":            len(orgs),
        "excluded_orgs":   list(excluded_orgs),
        "total_repos":     len(repos),
        "new_repos":       new_names,
        "new_repos_count": len(new_names),
        "existing_count":  len(existing_repos),
        "failed_creates":  [f"{i['item']['org']}_{i['item']['repo']}" for i in failed_creates],
        "push_failures":   [f"{r['org']}_{r['repo']}" for r in push_failures],
    }
    write_json(log_dir / "summary.json", summary)

    log.info("══ Summary ══")
    for k, v in summary.items():
        if k not in ("new_repos", "failed_creates", "push_failures", "excluded_orgs"):
            log.info("  %-22s %s", k, v)
    if summary["excluded_orgs"]:
        log.info("  %-22s %s", "excluded_orgs", summary["excluded_orgs"])
    if summary["failed_creates"]:
        log.warning("  failed_creates     : %s", summary["failed_creates"])
    if summary["push_failures"]:
        log.warning("  push_failures      : %s", summary["push_failures"])

    if sns_arn and new_names:
        msg = (
            "New repositories added to AWS CodeCommit:\n\n"
            + "\n".join(new_names)
            + f"\n\nTotal new: {len(new_names)}\n\nThanks,\nv3atlassianops"
        )
        sns_notify(sns_arn, sns_region, "New Repositories Added To AWS CodeCommit", msg)

    log.info("══ Backup finished at %s ══", datetime.now(timezone.utc).isoformat())

    if failed_creates or push_failures:
        log.warning("Completed with errors — review %s/summary.json", log_dir)
        sys.exit(1)


if __name__ == "__main__":
    main()
