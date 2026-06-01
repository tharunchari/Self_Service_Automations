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

def discover(token: str) -> tuple:
    log.info("── Phase 1: Discovery ──")
    raw_orgs = gh_paginate(f"/user/orgs?per_page={GH_PER_PAGE}", token)
    orgs     = [o["login"] for o in raw_orgs]
    log.info("Found %d organizations: %s", len(orgs), orgs)

    repos = []
    org_sizes = {}
    for org in orgs:
        raw  = gh_paginate(f"/orgs/{org}/repos?per_page={GH_PER_PAGE}&type=all", token)
        mine = [{"org": org, "repo": r["name"]} for r in raw]
        repos.extend(mine)
        org_sizes[org] = len(mine)
        log.info("  %-40s %d repos", org, len(mine))

    log.info("Total repositories discovered: %d", len(repos))
    return orgs, repos, org_sizes


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Clone / Update helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_bare_repo(repo_dir: Path) -> bool:
    return (
        (repo_dir / "HEAD").is_file()
        and (repo_dir / "HEAD").stat().st_size > 0
        and (repo_dir / "objects").is_dir()
        and (repo_dir / "refs").is_dir()
    )


def cc_repo_exists(cc_name: str, region: str) -> bool:
    rc, _, _ = run_cmd([
        "aws", "codecommit", "get-repository",
        "--repository-name", cc_name,
        "--region", region,
    ])
    return rc == 0


def clone_or_update_one(item: dict, base_dir: Path, gh_user: str,
                        gh_token: str, region: str) -> dict:
    org, repo = item["org"], item["repo"]
    org_dir   = base_dir / org
    repo_dir  = org_dir / f"{repo}.git"
    cc_name   = f"{org}_{repo}"
    org_dir.mkdir(parents=True, exist_ok=True)

    result = {"org": org, "repo": repo, "is_new": False,
              "clone_rc": None, "clone_error": ""}

    local_valid = repo_dir.is_dir() and is_valid_bare_repo(repo_dir)
    cc_exists   = local_valid and cc_repo_exists(cc_name, region)

    if local_valid and cc_exists:
        # ── Healthy on both sides: just sync ─────────────────────────────────
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
                time.sleep(RETRY_BACKOFF ** attempt)
        return result

    # ── Need to (re-)clone ────────────────────────────────────────────────────
    result["is_new"] = True

    if repo_dir.is_dir() and not local_valid:
        log.warning("  [%s/%s] Partial/corrupt clone — removing and re-cloning", org, repo)
        shutil.rmtree(repo_dir)
    elif repo_dir.is_dir() and local_valid and not cc_exists:
        # Local clone is good; CodeCommit repo is missing — skip re-clone
        log.info("  [%s/%s] Local clone OK but CodeCommit repo missing — will create + push", org, repo)
        result["clone_rc"] = 0
        return result

    clone_url = f"https://{gh_user}:{gh_token}@github.com/{org}/{repo}"
    for attempt in range(1, MAX_RETRIES + 1):
        rc, _, err = run_cmd(
            ["git", "clone", "--mirror", clone_url],
            cwd=str(org_dir), timeout=1800,
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Orchestrator  (chunked + state-aware)
# ─────────────────────────────────────────────────────────────────────────────

def phase_clone(repos: list, base_dir: Path, gh_user: str, gh_token: str,
                region: str, org_sizes: dict, state: State,
                log_dir: Path) -> tuple:
    """
    Processes repos in CHUNK_SIZE batches to cap peak RAM.
    Skips repos already marked complete in state.json.
    Auto-scales workers down for large orgs to avoid OOM.
    """
    log.info("── Phase 2: Clone / update %d repos (chunk=%d) ──",
             len(repos), CHUNK_SIZE)

    # Filter out already-completed repos
    pending = [r for r in repos if not state.is_done(r["org"], r["repo"])]
    skipped = len(repos) - len(pending)
    if skipped:
        log.info("  Skipping %d repos already completed in a previous run", skipped)

    new_repos, existing_repos = [], []
    clone_log = log_dir / "clone_results.jsonl"
    file_lock = threading.Lock()
    total_done = 0

    for chunk in chunks(pending, CHUNK_SIZE):
        # Pick worker count based on the largest org in this chunk
        max_org_size = max(org_sizes.get(r["org"], 0) for r in chunk)
        workers = CLONE_WORKERS_LARGE if max_org_size > ORG_SIZE_THRESHOLD else CLONE_WORKERS
        log.info("  Processing chunk of %d (workers=%d, max_org_size=%d)",
                 len(chunk), workers, max_org_size)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(clone_or_update_one, item, base_dir,
                            gh_user, gh_token, region): item
                for item in chunk
            }
            for future in as_completed(futures):
                res       = future.result()
                total_done += 1
                tag    = "NEW     " if res["is_new"] else "existing"
                status = "ok" if res["clone_rc"] == 0 else "FAILED"
                log.info("  [%d/%d] %-45s [%s] %s",
                         total_done + skipped, len(repos),
                         f"{res['org']}/{res['repo']}", tag, status)

                append_jsonl(clone_log, res, file_lock)

                if res["clone_rc"] == 0:
                    entry = {"org": res["org"], "repo": res["repo"]}
                    if res["is_new"]:
                        new_repos.append(entry)
                    else:
                        existing_repos.append(entry)
                        # Mark existing repos done immediately —
                        # new repos are marked done after push succeeds
                        state.mark_done(res["org"], res["repo"])

    failed = len(pending) - len(new_repos) - len(existing_repos)
    log.info("Clone phase: new=%d  existing=%d  failed=%d  skipped=%d",
             len(new_repos), len(existing_repos), failed, skipped)
    return new_repos, existing_repos


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Create CodeCommit repos (new only)
# ─────────────────────────────────────────────────────────────────────────────

def create_cc_repo(cc_name: str, region: str) -> tuple:
    rc, _, err = run_cmd([
        "aws", "codecommit", "create-repository",
        "--repository-name", cc_name,
        "--region", region,
    ])
    return rc == 0, err


def phase_create(new_repos: list, region: str, log_dir: Path) -> tuple:
    log.info("── Phase 3: Create CodeCommit repos for %d new repos ──", len(new_repos))
    ready, failed = [], []
    create_log = log_dir / "codecommit_create_results.jsonl"
    file_lock  = threading.Lock()

    for item in new_repos:
        cc_name = f"{item['org']}_{item['repo']}"
        ok, err = create_cc_repo(cc_name, region)
        append_jsonl(create_log, {"cc_name": cc_name, "success": ok, "error": err}, file_lock)
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
# ─────────────────────────────────────────────────────────────────────────────

def _cc_url(cc_user, cc_pass, region, cc_name):
    return (f"https://{cc_user}:{cc_pass}"
            f"@git-codecommit.{region}.amazonaws.com/v1/repos/{cc_name}")


def push_incremental_one(item, base_dir, cc_user, cc_pass, region, inc_script):
    org, repo = item["org"], item["repo"]
    cc_name   = f"{org}_{repo}"
    repo_dir  = base_dir / org / f"{repo}.git"
    result    = {"org": org, "repo": repo, "push_type": "incremental",
                 "push_rc": None, "push_error": ""}

    try:
        shutil.copy2(inc_script, repo_dir / "incremental-repo-migration.py")
    except Exception as exc:
        result["push_rc"] = 1
        result["push_error"] = str(exc)
        return result

    url = _cc_url(cc_user, cc_pass, region, cc_name)
    for attempt in range(1, MAX_RETRIES + 1):
        run_cmd(["git", "remote", "remove", "codecommit"], cwd=str(repo_dir))
        run_cmd(["git", "remote", "add",    "codecommit", url], cwd=str(repo_dir))
        rc, _, err = run_cmd(["python3", "incremental-repo-migration.py"],
                             cwd=str(repo_dir), timeout=3600)
        run_cmd(["git", "remote", "remove", "codecommit"], cwd=str(repo_dir))
        result["push_rc"] = rc
        if rc == 0:
            break
        result["push_error"] = mask(err, cc_pass, cc_user)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF ** attempt)
    return result


def push_mirror_one(item, base_dir, cc_user, cc_pass, region):
    org, repo = item["org"], item["repo"]
    cc_name   = f"{org}_{repo}"
    repo_dir  = base_dir / org / f"{repo}.git"
    url       = _cc_url(cc_user, cc_pass, region, cc_name)
    result    = {"org": org, "repo": repo, "push_type": "mirror",
                 "push_rc": None, "push_error": ""}

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
        if not val and not args.dry_run and name not in ("SNS_TOPIC_ARN",):
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

    base_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(log_dir / "backup.log")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(fh)

    started = datetime.now(timezone.utc).isoformat()
    log.info("══ GitHub → CodeCommit backup  started=%s  mode=%s ══",
             started, "DRY-RUN" if args.dry_run else "FULL")
    log.info("  base_dir=%s  region=%s  chunk=%d  clone_workers=%d/%d  push_workers=%d",
             base_dir, region, CHUNK_SIZE, CLONE_WORKERS, CLONE_WORKERS_LARGE, PUSH_WORKERS)

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    orgs, repos, org_sizes = discover(gh_token)

    if args.dry_run:
        report = {
            "mode":    "dry_run",
            "orgs":    orgs,
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
        if k not in ("new_repos", "failed_creates", "push_failures"):
            log.info("  %-22s %s", k, v)
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
