#!/bin/bash
 
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
 
# Fetch repos for both orgs
vitechsystems_repos=($(fetch_repos "$token_1" "$org_name_1"))
vitechinfra_repos=($(fetch_repos "$token_2" "$org_name_2"))
 
# Convert arrays to sets using associative arrays
declare -A org1_map
for repo in "${vitechsystems_repos[@]}"; do
  org1_map["$repo"]=1
done
 
# Find common repos and filtered repos
filtered_repos=()
common_repos=()
 
for repo in "${vitechinfra_repos[@]}"; do
  if [[ ${org1_map["$repo"]+exists} ]]; then
    common_repos+=("$repo")
  else
    filtered_repos+=("$repo")
  fi
done
 
# Print results
echo "Repos in $org_name_1:"
printf "%s\n" "${vitechsystems_repos[@]}"
 
echo -e "\nRepos in $org_name_2:"
printf "%s\n" "${vitechinfra_repos[@]}"
 
echo -e "\nCommon repos (in both orgs):"
printf "%s\n" "${common_repos[@]}"
 
echo -e "\nFiltered repos (unique to $org_name_2):"
printf "%s\n" "${filtered_repos[@]}"
