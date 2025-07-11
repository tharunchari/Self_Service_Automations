#!/bin/bash

page=1
repos=()
while true; do
  response=$(curl -s -H "Authorization: Bearer $TOKEN" "https://api.github.com/orgs/$ORG_NAME/repos?per_page=100&page=$page")
  current_repos=($(echo "$response" | jq -r '.[].name'))
  repos+=("${current_repos[@]}")
  # Break the loop if there are no more repositories
  if [ ${#current_repos[@]} -eq 0 ]; then
    break
  fi
  ((page++))
done
 
# Print only the repository names without additional formatting
echo "${repos[@]}"
