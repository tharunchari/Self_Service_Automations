#!/usr/bin/env python3
# ============================================================================
#  GitHub (ALL orgs discovered via PAT)  ->  AWS CodeCommit  backup / mirror
#  Python port of the Ansible playbook `majesco_github_backup.yml`.
# ============================================================================
#
#  WHAT CHANGED vs ANSIBLE
#  -----------------------
#  Ansible ran every step as a serial `loop:` and rebuilt its classification
#  lists with `set_fact ... default([]) + [item]` on every iteration (O(n^2)).
#  That is why adding the 3,500-repo Majesco-UWB org made it loop forever / fail.
#
#  This script keeps the SAME per-repo logic but runs the WHOLE pipeline
#  PER REPO inside a thread pool, so repos process in parallel, one bad repo
#  can never abort the run, and transient git/AWS/GitHub errors are retried.
#
#  ONE EXTERNAL DEPENDENCY
#  -----------------------
#  The incremental seed push is done by the repo's own script,
#  `incremental-repo-migration.py`, invoked EXACTLY as the playbook did:
#       cd <repo>.git
#       git remote remove codecommit            (ignore errors)
#       cp <incremental-repo-migration.py> ./incremental-repo-migration.py
#       git remote add codecommit <codecommit-url>
#       python incremental-repo-migration.py    (defaults: local=cwd, remote=codecommit)
#       git remote remove codecommit
#  It defaults to the repo path ../PYTHON_Script/incremental-repo-migration.py
#  (same as the playbook's incremental_script_src); override with
#  --incremental-script if needed.
#
#  EXCLUDING ORGS
#  --------------
#  Edit DEFAULT_EXCLUDED_ORGS (top of file) for a permanent skip list, and/or
#  pass --excluded-orgs "OrgA,OrgB" to add more at runtime. Majesco-UWB is now
#  processed; Majesco-Exaxe is excluded by default (edit as you like).
#
#  PER-REPO PIPELINE (identical decision logic to the playbook)
#  ------------------------------------------------------------
#    clone/update bare mirror
#      -> create CodeCommit repo (ignore "already exists")
#      -> classify:
#           NEW            create succeeded                       -> EMPTY -> incremental
#           EXISTING+EMPTY exists, list-branches empty            -> EMPTY -> incremental
#           EXISTING+FULL  exists, already has branches           -> mirror only
#           FAILED         create failed for a real reason        -> skipped + reported
#      -> incremental seed push  (EMPTY repos only, via the AWS script)
#      -> final `git push --mirror` (every repo that exists in CodeCommit)
#
#  PREREQS on the host : git, python, the incremental-repo-migration.py file,
#                        AND EITHER boto3 (pip install boto3) OR the aws CLI.
#  PAT scopes required : repo, read:org
#
#  USAGE
#  -----
#    python3 github_to_codecommit_backup.py \
#        --github-username  OAadmin_majesco \
#        --github-password  "$MAJESCO_CLASSIC_PAT" \
#        --codecommit-username "$AWS_CODECOMMIT_USERNAME" \
#        --codecommit-password "$AWS_CODECOMMIT_PASSWORD" \
#        --workers 12
#        # incremental-repo-migration.py is picked up from ../PYTHON_Script/ by default
#        # add more skips with:  --excluded-orgs "Majesco-Exaxe,Some-Other-Org"
#
#    # see what the huge org contains without touching anything:
#    ... --only-orgs Majesco-UWB --dry-run
# ============================================================================

import argparse
import concurrent.futures as futures
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# boto3 preferred for CodeCommit/SNS (clean exception types); falls back to the
# aws CLI (which this host has) if boto3 is not installed.
try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
    _HAVE_BOTO3 = True
except Exception:  # noqa: BLE001
    _HAVE_BOTO3 = False


# ---------------------------------------------------------------------------
# Organizations to ALWAYS skip. Edit this list to exclude more orgs.
# Note: Majesco-UWB is intentionally NOT here anymore — it is now processed.
# Anything passed via --excluded-orgs / EXCLUDED_ORGS is added ON TOP of this.
# ---------------------------------------------------------------------------
DEFAULT_EXCLUDED_ORGS = [
    # "Another-Org-To-Skip",
]

# Where the incremental script lives inside the checked-out GitHub repo,
# matching the playbook's `incremental_script_src: ../PYTHON_Script/...`.
DEFAULT_INCREMENTAL_SCRIPT = "../PYTHON_Script/incremental-repo-migration.py"


def resolve_incremental_script(path):
    """
    Resolve the incremental-repo-migration.py location. Tries (in order):
    the path as given (cwd-relative), relative to THIS script's dir, and the
    standard repo layout. Returns the first that exists, else the first
    candidate (startup check then errors with a clear message).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isabs(path):
        candidates = [path]
    else:
        candidates = [
            os.path.abspath(path),                                   # cwd-relative
            os.path.abspath(os.path.join(here, path)),               # script-relative
            os.path.abspath(os.path.join(here, "..", "PYTHON_Script",
                                         "incremental-repo-migration.py")),
            os.path.abspath(os.path.join(os.getcwd(), "PYTHON_Script",
                                         "incremental-repo-migration.py")),
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]


# ---------------------------------------------------------------------------
# Credential masking — applied to everything that reaches a log.
# ---------------------------------------------------------------------------
_CRED_RE = re.compile(r"//[^/@\s]+@")


def mask(text):
    if text is None:
        return text
    return _CRED_RE.sub("//***@", str(text))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("backup")


def setup_logging(log_dir, verbose):
    os.makedirs(log_dir, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    fh = logging.FileHandler(os.path.join(log_dir, "backup_run.log"))
    fh.setFormatter(fmt)
    log.addHandler(fh)


# ---------------------------------------------------------------------------
# Subprocess helper: timeout + retry, never raises on non-zero exit.
# ---------------------------------------------------------------------------
def run_cmd(args, cwd=None, timeout=3600, retries=2, backoff=5.0, env=None):
    full_env = os.environ.copy()
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")  # never block on a prompt
    full_env.setdefault("GIT_ASKPASS", "/bin/true")
    if env:
        full_env.update(env)

    last_rc, last_out, last_err = 1, "", ""
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(args, cwd=cwd, env=full_env, timeout=timeout,
                                  capture_output=True, text=True)
            last_rc, last_out, last_err = proc.returncode, proc.stdout, proc.stderr
            if proc.returncode == 0:
                return last_rc, last_out, last_err
            log.debug("cmd rc=%s (try %d/%d): %s | %s", proc.returncode,
                      attempt + 1, retries + 1, mask(" ".join(args)),
                      mask(last_err.strip()[:300]))
        except subprocess.TimeoutExpired:
            last_rc, last_err = 124, "timeout"
            log.debug("cmd TIMEOUT (try %d/%d): %s", attempt + 1, retries + 1,
                      mask(" ".join(args)))
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    return last_rc, last_out, last_err


# ===========================================================================
# GitHub discovery via REST API (replaces github_org_repos.sh, in-script)
# ===========================================================================
class GitHub:
    API = "https://api.github.com"

    def __init__(self, token):
        self.token = token

    def _get(self, url):
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "github-to-codecommit-backup")
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data, self._parse_next(resp.headers.get("Link", ""))
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):  # rate limited
                    wait = int(e.headers.get("Retry-After", "0") or 0)
                    if not wait:
                        reset = e.headers.get("X-RateLimit-Reset")
                        wait = max(1, int(reset) - int(time.time())) if reset else 30
                    wait = min(max(wait, 5), 120)
                    log.warning("GitHub rate-limited (%s); sleeping %ss.", e.code, wait)
                    time.sleep(wait)
                    continue
                if 500 <= e.code < 600:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
            except urllib.error.URLError:
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"GitHub GET failed after retries: {url}")

    @staticmethod
    def _parse_next(link_header):
        for part in link_header.split(","):
            seg = part.split(";")
            if len(seg) >= 2 and 'rel="next"' in seg[1]:
                return seg[0].strip().strip("<>")
        return None

    def _paginate(self, path):
        url, items = f"{self.API}{path}", []
        while url:
            data, url = self._get(url)
            items.extend(data if isinstance(data, list) else [data])
        return items

    def list_orgs(self):
        return sorted({o["login"] for o in self._paginate("/user/orgs?per_page=100")})

    def list_repos(self, org):
        return [r["name"]
                for r in self._paginate(f"/orgs/{org}/repos?type=all&per_page=100")]


# ===========================================================================
# AWS layer — boto3 if available, else `aws` CLI fallback.
# ===========================================================================
class Aws:
    def __init__(self, region, sns_region):
        self.region = region
        self.sns_region = sns_region
        if _HAVE_BOTO3:
            cfg = BotoConfig(retries={"max_attempts": 8, "mode": "adaptive"})
            self.cc = boto3.client("codecommit", region_name=region, config=cfg)
            self.sns = boto3.client("sns", region_name=sns_region, config=cfg)
            self.mode = "boto3"
        else:
            if not shutil.which("aws"):
                raise RuntimeError("Neither boto3 nor the aws CLI is available. "
                                   "Run `pip install boto3` or install the AWS CLI.")
            self.cc = self.sns = None
            self.mode = "cli"

    def create_repository(self, name):
        # -> "new" | "exists" | "failed:<reason>"
        if self.mode == "boto3":
            try:
                self.cc.create_repository(repositoryName=name)
                return "new"
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code == "RepositoryNameExistsException":
                    return "exists"
                return f"failed:{code or str(e)}"
            except Exception as e:  # noqa: BLE001
                return f"failed:{e}"
        rc, _o, err = run_cmd(["aws", "codecommit", "create-repository",
                               "--repository-name", name,
                               "--region", self.region], retries=2)
        if rc == 0:
            return "new"
        if "RepositoryNameExistsException" in err:
            return "exists"
        return f"failed:{err.strip()[:200] or 'rc=' + str(rc)}"

    def is_empty(self, name):
        # True only if the repo definitely has no branches.
        if self.mode == "boto3":
            try:
                return len(self.cc.list_branches(repositoryName=name)
                           .get("branches", [])) == 0
            except Exception:  # noqa: BLE001
                return False
        rc, out, _e = run_cmd(["aws", "codecommit", "list-branches",
                               "--repository-name", name, "--region", self.region,
                               "--query", "branches", "--output", "text"], retries=2)
        return rc == 0 and out.strip() in ("", "None")

    def sns_publish(self, topic_arn, subject, message):
        if self.mode == "boto3":
            self.sns.publish(TopicArn=topic_arn, Subject=subject, Message=message)
            return
        run_cmd(["aws", "sns", "publish", "--topic-arn", topic_arn,
                 "--region", self.sns_region, "--subject", subject,
                 "--message", message], retries=2)


# ===========================================================================
# Git operations
# ===========================================================================
def repo_url(user, secret, host_path):
    return f"https://{user}:{secret}@{host_path}"


def cc_host_path(cfg, name):
    return f"git-codecommit.{cfg['region']}.amazonaws.com/v1/repos/{name}"


def clone_or_update(cfg, org, repo):
    org_dir = os.path.join(cfg["clone_dir"], org)
    os.makedirs(org_dir, exist_ok=True)
    dest = os.path.join(org_dir, repo + ".git")
    gh_url = repo_url(cfg["github_username"], cfg["github_password"],
                      f"github.com/{org}/{repo}.git")
    if os.path.isdir(dest):
        run_cmd(["git", "-C", dest, "remote", "set-url", "origin", gh_url], retries=1)
        rc, _o, err = run_cmd(["git", "-C", dest, "remote", "update", "--prune"],
                              timeout=cfg["git_timeout"], retries=2)
    else:
        rc, _o, err = run_cmd(["git", "clone", "--mirror", gh_url, dest],
                              timeout=cfg["git_timeout"], retries=2)
    return dest, rc, err


def run_incremental_script(cfg, dest, name):
    """
    Replicate the playbook's incremental step EXACTLY, using AWS's
    incremental-repo-migration.py as the single external dependency.
    The script is run with no args from inside the repo dir, so its defaults
    (local repo = cwd, remote = 'codecommit') match how the playbook used it.
    """
    cc_url = repo_url(cfg["codecommit_username"], cfg["codecommit_password"],
                      cc_host_path(cfg, name))
    # start from a clean remote
    run_cmd(["git", "-C", dest, "remote", "remove", "codecommit"], retries=0)
    # copy the AWS script into the repo dir
    try:
        shutil.copyfile(cfg["incremental_script"],
                        os.path.join(dest, "incremental-repo-migration.py"))
    except Exception as e:  # noqa: BLE001
        return False, f"could not copy incremental script: {e}"
    run_cmd(["git", "-C", dest, "remote", "add", "codecommit", cc_url], retries=1)
    rc, out, err = run_cmd([cfg["python_bin"], "incremental-repo-migration.py"],
                           cwd=dest, timeout=cfg["incremental_timeout"], retries=1)
    run_cmd(["git", "-C", dest, "remote", "remove", "codecommit"], retries=0)
    return rc == 0, mask((err or out).strip()[:300])


def mirror_push(cfg, dest, org, repo, name):
    cc_url = repo_url(cfg["codecommit_username"], cfg["codecommit_password"],
                      cc_host_path(cfg, name))
    run_cmd(["git", "-C", dest, "fetch", "--prune"],
            timeout=cfg["git_timeout"], retries=2)
    # optional legacy feature-branch pruning, keyed by "<org>_<repo>"
    for pat in cfg["prune_branches"].get(name, []):
        rc, out, _e = run_cmd(["git", "-C", dest, "branch"], retries=1)
        if rc == 0:
            for b in (ln.strip(" *").strip() for ln in out.splitlines() if pat in ln):
                if b:
                    run_cmd(["git", "-C", dest, "branch", "-D", b], retries=1)
    rc, _o, err = run_cmd(["git", "-C", dest, "push", "--mirror", cc_url],
                          timeout=cfg["git_timeout"], retries=2)
    return rc == 0, mask(err.strip()[:200])


# ===========================================================================
# Per-repo pipeline (runs in parallel)
# ===========================================================================
def process_repo(cfg, aws, org, repo):
    name = f"{org}_{repo}"
    res = {"org": org, "repo": repo, "name": name, "clone_ok": False,
           "create_status": None, "empty": None, "incremental_ok": None,
           "mirror_ok": None, "is_new": False, "error": None}
    try:
        dest, rc, err = clone_or_update(cfg, org, repo)
        if rc != 0:
            res["error"] = "clone/update failed: " + mask(err.strip()[:200])
            log.warning("[%s] clone/update FAILED: %s", name, mask(err.strip()[:160]))
            return res
        res["clone_ok"] = True

        status = aws.create_repository(name)
        if status == "new":
            res["create_status"], res["is_new"], empty = "new", True, True
        elif status == "exists":
            res["create_status"] = "exists"
            empty = aws.is_empty(name)            # the "exists-but-empty" fix
        else:
            res["create_status"] = status         # "failed:<reason>"
            res["error"] = status
            log.warning("[%s] create FAILED: %s", name, status)
            return res
        res["empty"] = empty

        if empty:
            ok, msg = run_incremental_script(cfg, dest, name)
            res["incremental_ok"] = ok
            if not ok:
                log.warning("[%s] incremental push errors: %s", name, msg)

        ok, msg = mirror_push(cfg, dest, org, repo, name)
        res["mirror_ok"] = ok
        if not ok:
            res["error"] = "mirror push failed: " + msg
            log.warning("[%s] mirror push FAILED: %s", name, msg)
        else:
            log.info("[%s] OK (%s%s)", name, res["create_status"],
                     ", seeded" if empty else "")
    except Exception as e:  # noqa: BLE001 - never let one repo kill the run
        res["error"] = f"unexpected: {e}"
        log.exception("[%s] unexpected error", name)
    return res


# ===========================================================================
# Config / CLI
# ===========================================================================
def build_config():
    p = argparse.ArgumentParser(
        description="Parallel GitHub-org -> AWS CodeCommit mirror/backup.")
    env = os.environ.get

    p.add_argument("--github-username", default=env("GITHUB_USERNAME"))
    p.add_argument("--github-password", default=env("GITHUB_PASSWORD"),
                   help="GitHub PAT (scopes: repo, read:org)")
    p.add_argument("--codecommit-username", default=env("CODECOMMIT_USERNAME"))
    p.add_argument("--codecommit-password", default=env("CODECOMMIT_PASSWORD"))

    p.add_argument("--region", default=env("AWS_REGION", "us-west-2"))
    p.add_argument("--sns-region", default=env("SNS_REGION", "us-east-1"))
    p.add_argument("--sns-topic-arn",
                   default=env("SNS_TOPIC_ARN",
                               "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd"))

    p.add_argument("--base-dir", default=env("BASE_DIR", "/u02/github_to_codecommit"))
    p.add_argument("--clone-dir", default=env("CLONE_DIR"))
    p.add_argument("--log-dir", default=env("LOG_DIR"))

    p.add_argument("--incremental-script",
                   default=env("INCREMENTAL_SCRIPT", DEFAULT_INCREMENTAL_SCRIPT),
                   help="Path to incremental-repo-migration.py (the one dependency). "
                        "Defaults to the repo path ../PYTHON_Script/"
                        "incremental-repo-migration.py.")
    p.add_argument("--python-bin", default=env("PYTHON_BIN", "python"),
                   help="Interpreter used to run incremental-repo-migration.py "
                        "(matches the playbook's `python`).")

    p.add_argument("--excluded-orgs", default=env("EXCLUDED_ORGS", ""),
                   help="Comma-separated orgs to skip, ADDED ON TOP of the "
                        "DEFAULT_EXCLUDED_ORGS list near the top of this file.")
    p.add_argument("--only-orgs", default=env("ONLY_ORGS", ""),
                   help="Comma-separated allow-list (testing). Empty = all orgs.")

    p.add_argument("--workers", type=int, default=int(env("WORKERS", "10")),
                   help="Parallel repos in flight (default 10).")
    p.add_argument("--git-timeout", type=int, default=int(env("GIT_TIMEOUT", "7200")),
                   help="Per-git-operation timeout (s).")
    p.add_argument("--incremental-timeout", type=int,
                   default=int(env("INCREMENTAL_TIMEOUT", "21600")),
                   help="Timeout for one incremental-repo-migration.py run (s).")
    p.add_argument("--prune-branches-json", default=env("PRUNE_BRANCHES_JSON", "{}"),
                   help='JSON like {"Org_Repo": ["pattern1","pattern2"]}.')

    p.add_argument("--dry-run", action="store_true",
                   default=env("DRY_RUN", "").lower() in ("1", "true", "yes"))
    p.add_argument("--no-sns", action="store_true")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()

    base = a.base_dir
    return {
        "github_username": a.github_username,
        "github_password": a.github_password,
        "codecommit_username": a.codecommit_username,
        "codecommit_password": a.codecommit_password,
        "region": a.region, "sns_region": a.sns_region,
        "sns_topic_arn": a.sns_topic_arn,
        "base_dir": base,
        "clone_dir": a.clone_dir or os.path.join(base, "github_clone"),
        "log_dir": a.log_dir or os.path.join(base, "logs"),
        "incremental_script": resolve_incremental_script(a.incremental_script),
        "python_bin": a.python_bin,
        "excluded_orgs": sorted(set(DEFAULT_EXCLUDED_ORGS)
                                | {s.strip() for s in a.excluded_orgs.split(",")
                                   if s.strip()}),
        "only_orgs": [s.strip() for s in a.only_orgs.split(",") if s.strip()],
        "workers": max(1, a.workers),
        "git_timeout": a.git_timeout,
        "incremental_timeout": a.incremental_timeout,
        "prune_branches": json.loads(a.prune_branches_json or "{}"),
        "dry_run": a.dry_run, "no_sns": a.no_sns, "verbose": a.verbose,
    }


def require(cfg, keys):
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        sys.exit("Missing required settings: " + ", ".join(missing))


# ===========================================================================
# Main
# ===========================================================================
def main():
    cfg = build_config()
    os.makedirs(cfg["base_dir"], exist_ok=True)
    os.makedirs(cfg["clone_dir"], exist_ok=True)
    setup_logging(cfg["log_dir"], cfg["verbose"])

    started = datetime.now(timezone.utc)
    log.info("Backup started %s | dry_run=%s | workers=%d | aws=%s",
             started.isoformat(), cfg["dry_run"], cfg["workers"],
             "boto3" if _HAVE_BOTO3 else "aws-cli")

    require(cfg, ["github_username", "github_password"])

    # ---- DISCOVERY ----------------------------------------------------
    gh = GitHub(cfg["github_password"])
    log.info("Discovering organizations reachable by the PAT ...")
    orgs = gh.list_orgs()
    log.info("Found %d organizations: %s", len(orgs), orgs)
    log.info("Excluded organizations: %s", cfg["excluded_orgs"])
    if cfg["only_orgs"]:
        orgs = [o for o in orgs if o in cfg["only_orgs"]]
    orgs = [o for o in orgs if o not in cfg["excluded_orgs"]]
    log.info("Organizations to process (%d): %s", len(orgs), orgs)

    repos = []
    for org in orgs:
        names = gh.list_repos(org)
        log.info("  %s : %d repos", org, len(names))
        repos.extend((org, n) for n in names)
    log.info("Total repositories to process: %d", len(repos))

    if cfg["dry_run"]:
        report = {"orgs": orgs,
                  "per_org_counts": {o: sum(1 for r in repos if r[0] == o) for o in orgs},
                  "total_repos": len(repos)}
        path = os.path.join(cfg["log_dir"], "dry_run_discovery.json")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        log.info("DRY-RUN complete. Discovery report -> %s", path)
        return

    require(cfg, ["codecommit_username", "codecommit_password"])
    if not os.path.isfile(cfg["incremental_script"]):
        sys.exit("incremental-repo-migration.py not found at: "
                 + cfg["incremental_script"] + " (set --incremental-script)")
    aws = Aws(cfg["region"], cfg["sns_region"])

    # ---- PARALLEL PIPELINE -------------------------------------------
    results, done, total = [], 0, len(repos)
    lock = threading.Lock()
    log.info("Processing %d repos with %d parallel workers ...", total, cfg["workers"])
    with futures.ThreadPoolExecutor(max_workers=cfg["workers"]) as ex:
        fut_map = {ex.submit(process_repo, cfg, aws, o, r): (o, r) for o, r in repos}
        for fut in futures.as_completed(fut_map):
            res = fut.result()  # process_repo never raises
            with lock:
                results.append(res)
                done += 1
                if done % 50 == 0 or done == total:
                    log.info("Progress: %d/%d repos finished", done, total)

    # ---- SUMMARY ------------------------------------------------------
    new_names = [r["name"] for r in results if r["is_new"]]
    existing = [r for r in results if r["create_status"] == "exists"]
    seeded_existing = [r for r in existing if r["empty"]]
    failed_clone = [r["name"] for r in results if not r["clone_ok"]]
    failed_create = [r["name"] for r in results
                     if (r["create_status"] or "").startswith("failed")]
    failed_inc = [r["name"] for r in results if r["incremental_ok"] is False]
    failed_mirror = [r["name"] for r in results if r["mirror_ok"] is False]

    log.info("================ CLASSIFICATION SUMMARY ================")
    log.info("Total processed     : %d", len(results))
    log.info("NEW repos           : %d", len(new_names))
    log.info("EXISTING repos      : %d  (empty/seeded: %d)",
             len(existing), len(seeded_existing))
    log.info("Clone/update FAILED : %d -> %s", len(failed_clone), failed_clone)
    log.info("Create FAILED       : %d -> %s", len(failed_create), failed_create)
    log.info("Incremental FAILED  : %d -> %s", len(failed_inc), failed_inc)
    log.info("Mirror push FAILED  : %d -> %s", len(failed_mirror), failed_mirror)

    with open(os.path.join(cfg["log_dir"], "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    log.info("Per-repo results -> %s", os.path.join(cfg["log_dir"], "results.json"))

    # ---- SNS (only when new repos were added) -------------------------
    if new_names and not cfg["no_sns"]:
        message = ("New repositories have been added to AWS CodeCommit from GitHub:\n\n"
                   + "\n".join(new_names)
                   + f"\n\nTotal new repositories: {len(new_names)}\n\n"
                     "Please verify.\n\nThanks,\nITGS V3locity DevOps")
        try:
            aws.sns_publish(cfg["sns_topic_arn"],
                            "New Repositories Added To AWS CodeCommit", message)
            log.info("SNS notification sent for %d new repos.", len(new_names))
        except Exception as e:  # noqa: BLE001
            log.warning("SNS publish failed (non-fatal): %s", e)

    ended = datetime.now(timezone.utc)
    log.info("Backup finished %s (elapsed %s)", ended.isoformat(), ended - started)


if __name__ == "__main__":
    main()
