#!/bin/bash

token=$1
repo_name=$2
branch_name=$3
owner=$4
organization=vitechsystems

########################## PLEASE DO NOT CHANGE ANYTHING BELOW #####################

#Add branch protection rule to branch
if [ "$owner" == "client" ]; then
curl -X PUT \
  -H "Accept: application/vnd.github.v3+json" \
  -H "Authorization: Bearer $token" \
  -d '{
    "required_pull_request_reviews": {
      "dismiss_stale_reviews": true,
      "require_code_owner_reviews": true,
      "required_approving_review_count": 1
    },
    "enforce_admins": false,
    "required_status_checks": {
      "strict": false,
      "contexts": [
        "pre-merge/issue-valid"
      ]
    },
    "restrictions": null,
    "required_linear_history": true
  }' \
https://api.github.com/repos/$organization/$repo_name/branches/$branch_name/protection
else
curl -X PUT \
  -H "Accept: application/vnd.github.v3+json" \
  -H "Authorization: Bearer $token" \
  -d '{
    "required_pull_request_reviews": {
      "dismiss_stale_reviews": true,
      "require_code_owner_reviews": true,
      "required_approving_review_count": 1
    },
    "enforce_admins": false,
    "required_status_checks": {
      "strict": false,
      "contexts": [
        "pre-merge/branch-three-strike",
              "pre-merge/issue-approved",
              "pre-merge/issue-bcp",
              "pre-merge/issue-valid"
      ]
    },
    "restrictions": null,
    "required_linear_history": true
  }' \
https://api.github.com/repos/$organization/$repo_name/branches/$branch_name/protection
fi
