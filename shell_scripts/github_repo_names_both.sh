#!/bin/bash

set -euo pipefail

fetch_repos() {
  local TOKEN=$1
  local ORG_NAME=$2
  local page=1
  local repos=()

  while true; do
    response=$(curl -s -H "Authorization: Bearer $TOKEN" \
      "https://api.github.com/orgs/$ORG_NAME/repos?per_page=100&page=$page")
    current_repos=($(echo "$response" | jq -r '.[].name'))
    repos+=("${current_repos[@]}")
    if [ ${#current_repos[@]} -eq 0 ]; then
      break
    fi
    ((page++))
  done

  echo "${repos[@]}"
}

# Fetch repos
vitechsystems_repos=($(fetch_repos "$token_1" "$org_name_1"))
vitechinfra_repos=($(fetch_repos "$token_2" "$org_name_2"))

# Prepare maps and compare
declare -A org1_map
for repo in "${vitechsystems_repos[@]}"; do
  org1_map["$repo"]=1
done

filtered_repos=()
common_repos=()

for repo in "${vitechinfra_repos[@]}"; do
  if [[ ${org1_map["$repo"]+exists} ]]; then
    common_repos+=("$repo")
  else
    filtered_repos+=("$repo")
  fi
done

# Only print JSON output (so Ansible can parse it)
to_json_array() {
  printf '%s\n' "$@" | jq -R . | jq -s .
}

jq -n \
  --argjson org1 "$(to_json_array "${vitechsystems_repos[@]}")" \
  --argjson org2 "$(to_json_array "${vitechinfra_repos[@]}")" \
  --argjson common "$(to_json_array "${common_repos[@]}")" \
  --argjson unique_to_org2 "$(to_json_array "${filtered_repos[@]}")" \
  '{vitechsystems_repos: $org1, vitechinfra_repos: $org2, common_repos: $common, filtered_repos: $unique_to_org2}'
