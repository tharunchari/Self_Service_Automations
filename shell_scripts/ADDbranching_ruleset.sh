#!/bin/bash
 
# Define GitHub credentials and API details
#TOKEN="$GITHUB_TOKEN"
 
# Get organization name and repository name from arguments
ORG_NAME="$1"
REPO_NAME="$2"
 
# Prompt for the new branch name
NEW_BRANCH="$3"
REPO="$ORG_NAME/$REPO_NAME"
 
# Fetch all rulesets and filter for the ID using jq
RULESET_ID=$(curl -L -s \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/$REPO/rulesets" | jq -r '.[] | select(.name == "Branch Protection Ruleset") | .id')
 
# Check if the ruleset ID was found
if [ -n "$RULESET_ID" ]; then
  echo "Found ruleset ID: $RULESET_ID"
  # Fetch the current ruleset
  response=$(curl -L -s \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$REPO/rulesets/$RULESET_ID")
 
  # Update the include array with the new branch using jq
  updated_response=$(echo "$response" | jq --arg new_branch "$NEW_BRANCH" '.conditions.ref_name.include += [$new_branch]')
 
  # Ensure the 'target' field and any other necessary fields are set correctly
  updated_response=$(echo "$updated_response" | jq '.target |= "branch"')
 
  # Send the updated ruleset back to GitHub
  curl -L \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$REPO/rulesets/$RULESET_ID" \
    -d "$updated_response"
 
else
  echo "No ruleset with the name 'Branch Protection Ruleset' found. Creating a new one..."
 
  # Define source details
  SOURCE_REPO="vitechsystems/CoreAdmin"
  SOURCE_RULESET_ID="1149284"
  NEW_RULESET_NAME="Branch Protection Ruleset"
 
  # Fetch the existing ruleset
  existing_ruleset=$(curl -L -s \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$SOURCE_REPO/rulesets/$SOURCE_RULESET_ID")
 
  # Modify the ruleset JSON for the target repository
  new_ruleset=$(echo "$existing_ruleset" | jq --arg name "$NEW_RULESET_NAME" --arg target_repo "$ORG_NAME/$REPO_NAME" --arg new_branch "$NEW_BRANCH" '
    .id = null |                  # Remove the ID
    .name = $name |               # Set the new name for the ruleset
    .source = $target_repo |      # Update the source repository
    .created_at = null |          # Remove timestamps
    .updated_at = null |
    .node_id = null |
    .target = "branch" |          # Ensure target is set (example value)
    .enforcement = "active" |     # Ensure enforcement field is included
    .conditions.ref_name.include = [$new_branch] # Add the new branch
  ')
 
  # Create the new ruleset in the target repository
  response=$(curl -L -s \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$ORG_NAME/$REPO_NAME/rulesets" \
    -d "$new_ruleset")
 
  echo "Response from creating new ruleset:"
  echo "$response"
fi
