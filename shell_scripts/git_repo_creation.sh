#!/bin/bash

token=$1
repo_name=$2
owner=$3
ticket_type=$4
secret_code=$5
organization=vitechsystems


########################## PLEASE DO NOT CHANGE ANYTHING BELOW #####################

team_id_1="vitech-devops-svc-account"
permission_1="Super Users"
if [ "$organization" == "vitechsystems" ]; then
    team_actor_id_1="7402378"
else
    team_actor_id_1="7908198"
fi

team_id_2="vitech-devops-team"
permission_2="Super Users"
if [ "$organization" == "vitechsystems" ]; then
    team_actor_id_2="7303737"
else
    team_actor_id_2="7908182"
fi

team_id_3="vitech-product-developers"
permission_3="push"
team_actor_id_3="8297051"
if [ "$organization" == "vitechsystems" ]; then
    team_actor_id_3="7303728"
else
    team_actor_id_3="8297051"
fi

team_id_4="vitech-ng-developers"
permission_4="push"
if [ "$organization" == "vitechsystems" ]; then
    team_actor_id_4="7303736"
else
    team_actor_id_4="7927606"
fi

team_id_5="vitech-client-developers"
if [ "$organization" == "vitechsystems" ]; then
    team_actor_id_5="7303727"
else
    team_actor_id_5="8195011"
fi

team_id_6="vitech-client-app-ops"
if [ "$organization" == "vitechsystems" ]; then
    team_actor_id_6="9058202"
else
    team_actor_id_6="9327888"
fi

if [ "$organization" == "vitechsystems" ]; then
    webhook_url="https://kix4g7xxor35kastx66mnxebta0hidqk.lambda-url.us-east-1.on.aws?project=$ticket_type"
else
    webhook_url="https://hmud52czmdwdvrwddtrjispity0mnrre.lambda-url.us-east-1.on.aws?project=$ticket_type"
fi

# Set team permissions for repositories
if [ "$owner" == "client" ]; then
    permission_5="push"
else
    permission_5="pull"
fi

# Set team permissions for repositories
if [ "$owner" == "client" ]; then
    permission_6="Super Users"
else
    permission_6="pull"
fi

#Repository Creation
curl -X POST \
  -H "Authorization: token $token" \
  -H "Accept: application/vnd.github.v3+json" \
  -d "{\"name\": \"$repo_name\", \"private\": true}" \
  "https://api.github.com/orgs/$organization/repos"

#Making repo Changes
curl -L \
    -X PATCH \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    https://api.github.com/repos/$organization/$repo_name \
    -d '{"allow_merge_commit":false,"use_squash_pr_title_as_default": true,"squash_merge_commit_title": "PR_TITLE","has_issues": false}'

#Set team permissions for repositories 
curl -L \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d "{\"permission\":\"$permission_1\"}" \
    "https://api.github.com/orgs/$organization/teams/$team_id_1/repos/$organization/$repo_name"

#Set team permissions for repositories 
curl -L \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d "{\"permission\":\"$permission_2\"}" \
    "https://api.github.com/orgs/$organization/teams/$team_id_2/repos/$organization/$repo_name"

#Set team permissions for repositories 
curl -L \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d "{\"permission\":\"$permission_3\"}" \
    "https://api.github.com/orgs/$organization/teams/$team_id_3/repos/$organization/$repo_name"

#Set team permissions for repositories 
curl -L \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d "{\"permission\":\"$permission_4\"}" \
    "https://api.github.com/orgs/$organization/teams/$team_id_4/repos/$organization/$repo_name"

#Set team permissions for repositories 
curl -L \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d "{\"permission\":\"$permission_5\"}" \
    "https://api.github.com/orgs/$organization/teams/$team_id_5/repos/$organization/$repo_name"

#Set team permissions for repositories 
curl -L \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d "{\"permission\":\"$permission_6\"}" \
    "https://api.github.com/orgs/$organization/teams/$team_id_6/repos/$organization/$repo_name"

# Adding Disable Branch Creation Ruleset
if [ "$owner" == "client" ]; then
curl -L \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d '{"name":"Disable Branch Creation","target":"branch","enforcement":"active","bypass_actors":[{"actor_id":1,"actor_type":"OrganizationAdmin","bypass_mode":"always"},{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"always"},{"actor_id":'$team_actor_id_1',"actor_type":"Team","bypass_mode":"always"},{"actor_id":'$team_actor_id_2',"actor_type":"Team","bypass_mode":"always"},{"actor_id":'$team_actor_id_6',"actor_type":"Team","bypass_mode":"always"}],"conditions":{"ref_name":{"include":["~ALL"],"exclude":[]}},"rules":[{"type":"creation"},{"type":"deletion"},{"type":"non_fast_forward"}]}' \
    "https://api.github.com/repos/$organization/$repo_name/rulesets"

else
curl -L \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $token" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d '{"name":"Disable Branch Creation","target":"branch","enforcement":"active","bypass_actors":[{"actor_id":1,"actor_type":"OrganizationAdmin","bypass_mode":"always"},{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"always"},{"actor_id":'$team_actor_id_1',"actor_type":"Team","bypass_mode":"always"},{"actor_id":'$team_actor_id_2',"actor_type":"Team","bypass_mode":"always"}],"conditions":{"ref_name":{"include":["~ALL"],"exclude":[]}},"rules":[{"type":"creation"},{"type":"deletion"},{"type":"non_fast_forward"}]}' \
    "https://api.github.com/repos/$organization/$repo_name/rulesets"

fi

# Adding webhook url
curl -L \
      -X POST \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer $token" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      -d '{"name":"web","active":true,"events":["pull_request_review","pull_request","pull_request_review_comment"],"config":{"url":"'"$webhook_url"'","content_type":"json","insecure_ssl":"0","secret":"'"$secret_code"'"}}' \
      "https://api.github.com/repos/$organization/$repo_name/hooks"

# Adding Autolink to repo
curl -L \
      -X POST \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer $token" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      -d '{"key_prefix":"'"$ticket_type"'-","url_template":"https://jira.vitechinc.com/jira/browse/'"$ticket_type"'-<num>","is_alphanumeric":true}' \
      "https://api.github.com/repos/$organization/$repo_name/autolinks"
