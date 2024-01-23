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
        "dismiss_stale_reviews": false,
        "require_code_owner_reviews": false,
        "required_approving_review_count": 1
      },
      "enforce_admins": false,
      "required_status_checks": {
        "strict": true,
        "contexts": [
          "pre-merge/issue-valid"
          ]
      },
      "restrictions": null,
      "required_linear_history": true,
      "required_protected_branches": [
        {
          "pattern": "$branch_name",
          "required_status_checks": {
            "strict": true,
            "contexts": [
              "pre-merge/issue-valid"
            ]
          }
        }
      ]
    }' \
    https://api.github.com/repos/$organization/$repo_name/branches/$branch_name/protection
else
curl -X PUT \
    -H "Accept: application/vnd.github.v3+json" \
    -H "Authorization: Bearer $token" \
    -d '{
      "required_pull_request_reviews": {
        "dismiss_stale_reviews": false,
        "require_code_owner_reviews": false,
        "required_approving_review_count": 1
      },
      "enforce_admins": false,
      "required_status_checks": {
        "strict": true,
        "contexts": [
              "pre-merge/branch-three-strike",
              "pre-merge/issue-approved",
              "pre-merge/issue-bcp",
              "pre-merge/issue-valid"
            ]
      },
      "restrictions": null,
      "required_linear_history": true,
      "required_protected_branches": [
        {
          "pattern": "{{ branch_name_1 }}",
          "required_status_checks": {
          "strict": true,
            "contexts": [
              "pre-merge/branch-three-strike",
              "pre-merge/issue-approved",
              "pre-merge/issue-bcp",
              "pre-merge/issue-valid"
            ]
          }
        }
      ]
    }' \
    https://api.github.com/repos/$organization/$repo_name/branches/$branch_name/protection
fi
