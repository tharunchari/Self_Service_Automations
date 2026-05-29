#!/usr/bin/env bash
# github_org_repos.sh
#
# Discovers every GitHub organization reachable by the given PAT and lists
# every repository in each org. Handles pagination (100/page) for 3k+ orgs.
# Streams all large data through files — never through shell arguments —
# to stay safely under Linux ARG_MAX at any repo count.
#
# INPUT  (environment):
#   GITHUB_TOKEN   — Classic PAT with scopes: repo, read:org
#
# OUTPUT (stdout):
#   Single JSON document:
#   {
#     "orgs": ["org1", "org2", ...],
#     "repos": [
#       {"org": "org1", "repo": "repo-name"},
#       ...
#     ]
#   }
#
# All progress / diagnostic messages → stderr (stdout stays clean for Ansible)
# Requirements: bash >= 4, curl, jq

set -eo pipefail

# ── validate inputs ────────────────────────────────────────────────────────────
if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "[ERROR] GITHUB_TOKEN environment variable is not set." >&2
  exit 1
fi
command -v curl >/dev/null 2>&1 || { echo "[ERROR] curl is required." >&2; exit 1; }
command -v jq   >/dev/null 2>&1 || { echo "[ERROR] jq is required."   >&2; exit 1; }

# ── config ─────────────────────────────────────────────────────────────────────
API="https://api.github.com"
PER_PAGE=100

# ── temp workspace (auto-cleaned on exit) ──────────────────────────────────────
WORKDIR=$(mktemp -d)
trap 'rm -rf "${WORKDIR}"' EXIT

ORGS_FILE="${WORKDIR}/orgs.json"       # JSON array of org login strings
REPOS_FILE="${WORKDIR}/repos.jsonl"    # one {"org":..,"repo":..} object per line
touch "${REPOS_FILE}"

# ── helper: authenticated GET with retry ───────────────────────────────────────
# Writes response body to stdout; exits non-zero on HTTP error.
gh_get() {
  local url="$1"
  local tmp_resp="${WORKDIR}/resp_$$.json"

  local http_code
  http_code=$(curl -sS --retry 3 --retry-delay 2 --retry-max-time 30 \
    --write-out "%{http_code}" \
    --output "${tmp_resp}" \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${url}")

  if [ "${http_code}" -lt 200 ] || [ "${http_code}" -ge 300 ]; then
    echo "[ERROR] GitHub API returned HTTP ${http_code} for: ${url}" >&2
    cat "${tmp_resp}" >&2
    rm -f "${tmp_resp}"
    exit 1
  fi

  cat "${tmp_resp}"
  rm -f "${tmp_resp}"
}

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Discover all organizations the PAT can see (paginated)
# ══════════════════════════════════════════════════════════════════════════════
echo "[INFO] Fetching organizations..." >&2

# Accumulate org logins into a temp JSONL file, then build the array once.
ORGS_JSONL="${WORKDIR}/orgs.jsonl"
touch "${ORGS_JSONL}"
page=1

while true; do
  resp=$(gh_get "${API}/user/orgs?per_page=${PER_PAGE}&page=${page}")
  count=$(echo "${resp}" | jq 'length')
  [ "${count}" -eq 0 ] && break

  # One login per line → JSONL (no shell arg passing)
  echo "${resp}" | jq -r '.[].login' >> "${ORGS_JSONL}"

  echo "[INFO]   page ${page}: got ${count} org(s)" >&2
  page=$((page + 1))
done

# Build orgs JSON array from JSONL file entirely through a pipe
jq -R . < "${ORGS_JSONL}" | jq -s '.' > "${ORGS_FILE}"

total_orgs=$(jq 'length' "${ORGS_FILE}")
echo "[INFO] Total organizations found: ${total_orgs}" >&2

if [ "${total_orgs}" -eq 0 ]; then
  echo "[WARN] No organizations found. Check that read:org scope is granted." >&2
  printf '{"orgs":[],"repos":[]}\n'
  exit 0
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — List every repository in every org (paginated)
# Streams directly to REPOS_FILE (JSONL), never through shell args.
# ══════════════════════════════════════════════════════════════════════════════
total_repos=0

# Read org names from file (not from a shell variable) to avoid ARG_MAX
while IFS= read -r org; do
  [ -z "${org}" ] && continue
  echo "[INFO] Fetching repos for org: ${org}" >&2
  org_count=0
  page=1

  while true; do
    resp=$(gh_get "${API}/orgs/${org}/repos?per_page=${PER_PAGE}&page=${page}&type=all")
    count=$(echo "${resp}" | jq 'length')
    [ "${count}" -eq 0 ] && break

    # Stream each repo as a {"org":..,"repo":..} line straight to the JSONL file
    echo "${resp}" | jq -c --arg org "${org}" '.[] | {org: $org, repo: .name}' >> "${REPOS_FILE}"

    org_count=$((org_count + count))
    page=$((page + 1))
  done

  total_repos=$((total_repos + org_count))
  echo "[INFO]   ${org}: ${org_count} repos" >&2

done < "${ORGS_JSONL}"

echo "[INFO] Total repositories found across all orgs: ${total_repos}" >&2

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Emit final JSON to stdout
#
# Key design: use jq --slurpfile to read from files (not --argjson which
# passes data as a CLI argument and crashes at ~2MB / ~4000 repos).
# --slurpfile wraps the file contents in an array, so we unwrap with .[0].
# ══════════════════════════════════════════════════════════════════════════════

# Convert REPOS_FILE (JSONL) to a proper JSON array in a temp file
REPOS_JSON="${WORKDIR}/repos_array.json"
jq -s '.' < "${REPOS_FILE}" > "${REPOS_JSON}"

# Build final output: jq reads both arrays from files, not from args
jq -n \
  --slurpfile orgs  "${ORGS_FILE}"  \
  --slurpfile repos "${REPOS_JSON}" \
  '{orgs: $orgs[0], repos: $repos[0]}'
