
#!/bin/bash
 
# GitHub token and organization details

ORG=$2
REPOS=$3
ORG_NAME=$1
 
# GitHub API endpoint for the repository
API_URL="https://api.github.com/repos/$ORG/$REPOS/rulesets"
AUTH_HEADER="Authorization: Bearer $GITHUB_TOKEN"
 
# Function to check if a ruleset already exists
check_ruleset_exists() {
  local ruleset_name=$1
  existing_ruleset=$(curl -s -H "Accept: application/vnd.github+json" -H "$AUTH_HEADER" $API_URL | grep -o "\"name\": \"$ruleset_name\"")
 
  if [ ! -z "$existing_ruleset" ]; then
    echo "Ruleset '$ruleset_name' already exists."
    return 0
  else
    return 1
  fi
}
 
# Function to create a ruleset
create_ruleset() {
  local payload=$1
 
  curl -L -X POST -H "Accept: application/vnd.github+json" -H "$AUTH_HEADER" -H "X-GitHub-Api-Version: 2022-11-28" $API_URL -d "$payload"
}
 
# Determine which ruleset to check and create
if [ "$ORG_NAME" == "prod" ]; then
  RULESET_NAME="Branch Protection Ruleset"
  PAYLOAD='{
    "name": "Branch Protection Ruleset",
    "target": "branch",
    "enforcement": "active",
    "conditions": {
      "ref_name": {
        "exclude": [],
        "include": ["~ALL"]
      }
    },
    "rules": [
      {
        "type": "deletion"
      },
      {
        "type": "required_linear_history"
      },
      {
        "type": "pull_request",
        "parameters": {
          "required_approving_review_count": 1,
          "dismiss_stale_reviews_on_push": false,
          "require_code_owner_review": true,
          "require_last_push_approval": false,
          "required_review_thread_resolution": false
        }
      },
      {
        "type": "required_status_checks",
        "parameters": {
          "strict_required_status_checks_policy": false,
          "do_not_enforce_on_create": false,
          "required_status_checks": [
            {"context": "CodeQL scan for Java"},
            {"context": "CodeQL scan for JavaScript"},
            {"context": "CodeQLScanStatusJava"},
            {"context": "CodeQLScanStatusJavaScript"},
            {"context": "CodeQL"},
            {"context": "LaunchingSelfHostedRunner_1"},
            {"context": "LaunchingSelfHostedRunner_2"},
            {"context": "TerminatingSelfHostedRunner_1"},
            {"context": "TerminatingSelfHostedRunner_2"},
            {"context": "dependency-review"},
            {"context": "pre-merge/issue-valid"},
            {"context": "pre-merge/branch-three-strike"},
            {"context": "pre-merge/issue-approved"},
            {"context": "pre-merge/issue-bcp"}
          ]
        }
      }
    ]
  }'
elif [ "$ORG_NAME" == "client" ]; then
  RULESET_NAME="Branch Protection Ruleset"
  PAYLOAD='{
    "name": "Branch Protection Ruleset",
    "target": "branch",
    "enforcement": "active",
    "conditions": {
      "ref_name": {
        "exclude": [],
        "include": ["~ALL"]
      }
    },
    "rules": [
      {
        "type": "deletion"
      },
      {
        "type": "required_linear_history"
      },
      {
        "type": "pull_request",
        "parameters": {
          "required_approving_review_count": 1,
          "dismiss_stale_reviews_on_push": false,
          "require_code_owner_review": true,
          "require_last_push_approval": false,
          "required_review_thread_resolution": false
        }
      },
      {
        "type": "required_status_checks",
        "parameters": {
          "strict_required_status_checks_policy": false,
          "do_not_enforce_on_create": false,
          "required_status_checks": [
            {"context": "CodeQL scan for Java"},
            {"context": "CodeQL scan for JavaScript"},
            {"context": "CodeQLScanStatusJava"},
            {"context": "CodeQLScanStatusJavaScript"},
            {"context": "CodeQL"},
            {"context": "LaunchingSelfHostedRunner_1"},
            {"context": "LaunchingSelfHostedRunner_2"},
            {"context": "TerminatingSelfHostedRunner_1"},
            {"context": "TerminatingSelfHostedRunner_2"},
            {"context": "dependency-review"},
            {"context": "pre-merge/issue-valid"}
          ]
        }
      }
    ]
  }'
else
  echo "Invalid organization name. Please provide 'prod' or 'client'."
  exit 1
fi
 
# Check if the ruleset exists and create it if not
if check_ruleset_exists "$RULESET_NAME"; then
  echo "No action needed."
else
  create_ruleset "$PAYLOAD"
  echo "Ruleset '$RULESET_NAME' created."
fi
