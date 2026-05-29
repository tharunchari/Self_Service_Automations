#!/usr/bin/env bash
# github_org_repos.sh
#
# Discovers every GitHub organization reachable by the given PAT and lists
# every repository in each org. Handles pagination so orgs with 3k+ repos
# are fully enumerated (100 repos per API page).
#
# INPUT  (environment variables):
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
# All progress / diagnostic messages go to stderr so stdout stays clean
# for Ansible's from_json filter.
#
# Requirements on the host: bash >= 4, curl, jq

set -eo pipefail

# ---- validate input -------------------------------------------------------
if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "[ERROR] GITHUB_TOKEN environment variable is not set." >&2
  exit 1
fi

command -v curl >/dev/null 2>&1 || { echo "[ERROR] curl is required but not found." >&2; exit 1; }
command -v jq   >/dev/null 2>&1 || { echo "[ERROR] jq is required but not found."   >&2; exit 1; }

# ---- config ---------------------------------------------------------------
API="https://api.github.com"
PER_PAGE=100

# ---- helper: authenticated GET with retry ---------------------------------
gh_get() {
  local url="$1"
  local http_code
  local response

  response=$(curl -sS --retry 3 --retry-delay 2 --retry-max-time 30 \
    --write-out "\n%{http_code}" \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${url}")

  http_code=$(echo "${response}" | tail -n1)
  body=$(echo "${response}" | head -n -1)

  if [ "${http_code}" -lt 200 ] || [ "${http_code}" -ge 300 ]; then
    echo "[ERROR] GitHub API returned HTTP ${http_code} for: ${url}" >&2
    echo "[ERROR] Response: ${body}" >&2
    exit 1
  fi

  echo "${body}"
}

# ---- temp file (auto-cleaned on exit) -------------------------------------
tmp_repos=$(mktemp)
trap 'rm -f "${tmp_repos}"' EXIT

# ===========================================================================
# STEP 1 — Discover all organizations the PAT can see (paginated)
# ===========================================================================
echo "[INFO] Fetching organizations..." >&2
orgs=()
page=1

while true; do
  resp=$(gh_get "${API}/user/orgs?per_page=${PER_PAGE}&page=${page}")
  count=$(echo "${resp}" | jq 'length')
  [ "${count}" -eq 0 ] && break

  while IFS= read -r login; do
    [ -n "${login}" ] && orgs+=("${login}")
  done < <(echo "${resp}" | jq -r '.[].login')

  echo "[INFO]   page ${page}: got ${count} org(s)" >&2
  page=$((page + 1))
done

total_orgs="${#orgs[@]}"
echo "[INFO] Total organizations found: ${total_orgs}" >&2

if [ "${total_orgs}" -eq 0 ]; then
  echo "[WARN] No organizations found for this PAT. Check that read:org scope is granted." >&2
  echo '{"orgs":[],"repos":[]}'
  exit 0
fi

# ===========================================================================
# STEP 2 — List every repository in every org (paginated)
# ===========================================================================
total_repos=0

for org in "${orgs[@]}"; do
  echo "[INFO] Fetching repos for org: ${org}" >&2
  org_repo_count=0
  page=1

  while true; do
    resp=$(gh_get "${API}/orgs/${org}/repos?per_page=${PER_PAGE}&page=${page}&type=all")
    count=$(echo "${resp}" | jq 'length')
    [ "${count}" -eq 0 ] && break

    # Append each repo as a JSON line to the temp file
    echo "${resp}" | jq -c --arg org "${org}" '.[] | {org: $org, repo: .name}' >> "${tmp_repos}"

    org_repo_count=$((org_repo_count + count))
    page=$((page + 1))
  done

  total_repos=$((total_repos + org_repo_count))
  echo "[INFO]   ${org}: ${org_repo_count} repos" >&2
done

echo "[INFO] Total repositories found across all orgs: ${total_repos}" >&2

# ===========================================================================
# STEP 3 — Emit single JSON document to stdout (only this goes to stdout)
# ===========================================================================
orgs_json=$(printf '%s\n' "${orgs[@]}" | jq -R . | jq -s 'map(select(. != ""))')
repos_json=$(jq -s '.' "${tmp_repos}")

jq -n \
  --argjson orgs  "${orgs_json}"  \
  --argjson repos "${repos_json}" \
  '{orgs: $orgs, repos: $repos}'
