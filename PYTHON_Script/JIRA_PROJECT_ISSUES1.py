import csv
import boto3
from jira import JIRA
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from botocore.exceptions import ClientError
import os
from datetime import datetime, timedelta
 
# -------------------- Configuration --------------------
JIRA_URL = "https://jiraupg.vitechinc.com/jiraupg"
JIRA_USERNAME = "svallabhuni"
JIRA_PASSWORD = os.environ.get("JIRA_PASSWORD")
JIRA_PROJECT = os.environ.get("JIRA_PROJECT", "").strip()
DURATION_MONTHS = os.environ.get("DURATION_MONTHS", "").strip()
JIRA_JQL = os.environ.get("JIRA_JQL", "").strip()
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SES_SENDER = "v3atlassianops@vitechinc.com"
SES_RECIPIENT = "v3atlassianops@vitechinc.com"
 
# -------------------- Jira Authentication --------------------
jira = JIRA(server=JIRA_URL, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
 
# -------------------- Chunk Utility --------------------
def chunk_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]
 
# -------------------- Build the JQL Query --------------------
issue_keys = []
 
if JIRA_JQL and JIRA_JQL.startswith("issues in (") and JIRA_JQL.endswith(")"):
    # Parse issue keys from the custom JQL
    raw_keys = JIRA_JQL[len("issues in ("):-1]
    issue_keys = [key.strip() for key in raw_keys.split(",") if key.strip()]
    mode_label = "chunked"
    filename_suffix = "chunked_issues"
    print(f"📥 Using chunked 'issues in (...)' with {len(issue_keys)} keys")
else:
    if JIRA_JQL:
        jql_query = JIRA_JQL
        mode_label = "custom"
        filename_suffix = "custom_jql"
        print(f"📥 Using custom JQL:\n{jql_query}")
    elif JIRA_PROJECT:
        try:
            months = int(DURATION_MONTHS or "3")
        except ValueError:
            months = 3
        today = datetime.utcnow()
        past_date = today - timedelta(days=months * 30)
        created_after = past_date.strftime("%Y-%m-%d")
        jql_query = f"project = {JIRA_PROJECT} AND created >= '{created_after}' ORDER BY created DESC"
        mode_label = f"{months}m"
        filename_suffix = f"{JIRA_PROJECT.lower()}_{months}m"
        print(f"📥 Using default query:\n{jql_query}")
    else:
        raise ValueError("❌ ERROR: Either 'jira_jql' or 'jira_project' must be provided.")
 
# -------------------- Fetch Issues from Jira --------------------
all_issues = []
print(f"\n📡 Fetching issues from Jira...\n")
 
if issue_keys:
    for i, chunk in enumerate(chunk_list(issue_keys, 200), start=1):
        jql_chunk = f"issue in ({','.join(chunk)})"
        print(f"🔎 Querying chunk {i}: {len(chunk)} issues")
        start_at = 0
        max_results = 100
        while True:
            issues = jira.search_issues(jql_chunk, startAt=start_at, maxResults=max_results)
            if not issues:
                break
            all_issues.extend(issues)
            print(f"  ↪ Chunk {i}: Fetched {len(all_issues)} issues so far...")
            start_at += max_results
else:
    start_at = 0
    max_results = 100
    while True:
        issues = jira.search_issues(jql_query, startAt=start_at, maxResults=max_results)
        if not issues:
            break
        all_issues.extend(issues)
        print(f"Fetched {len(all_issues)} issues so far...")
        start_at += max_results
 
print(f"\n✅ Done. Total issues fetched: {len(all_issues)}")
 
# -------------------- Export to CSV --------------------
CSV_FILENAME = f"jira_issues_{filename_suffix}.csv"
print(f"💾 Exporting to {CSV_FILENAME}...\n")
 
with open(CSV_FILENAME, mode="w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["Key", "Summary", "Status", "Assignee", "Created"])
    for issue in all_issues:
        writer.writerow([
            issue.key,
            issue.fields.summary,
            issue.fields.status.name,
            issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned",
            issue.fields.created
        ])
 
print(f"📄 Export completed: {CSV_FILENAME}")
 
# -------------------- Send Email via AWS SES --------------------
try:
    ses = boto3.client("ses", region_name=AWS_REGION)
    msg = MIMEMultipart()
    msg["Subject"] = f"Jira Issue Report - {mode_label}"
    msg["From"] = SES_SENDER
    msg["To"] = SES_RECIPIENT
 
    email_body = f"Attached is the Jira issue report using the following query:\n\n{JIRA_JQL or jql_query}"
    msg.attach(MIMEText(email_body, "plain"))
 
    with open(CSV_FILENAME, "rb") as file:
        part = MIMEApplication(file.read())
        part.add_header("Content-Disposition", f"attachment; filename={CSV_FILENAME}")
        msg.attach(part)
 
    response = ses.send_raw_email(
        Source=SES_SENDER,
        Destinations=[SES_RECIPIENT],
        RawMessage={"Data": msg.as_string()}
    )
    print("📧 Email sent successfully via AWS SES.")
except ClientError as e:
    print(f"❌ Failed to send email: {e}")
