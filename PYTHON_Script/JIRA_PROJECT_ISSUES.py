import csv

import boto3

from jira import JIRA

from email.mime.multipart import MIMEMultipart

from email.mime.text import MIMEText

from email.mime.application import MIMEApplication

from botocore.exceptions import ClientError

import os

# -------------------- Configuration --------------------

JIRA_URL = "https://jira.vitechinc.com/jira"       # Update if needed

JIRA_USERNAME = "svallabhuni"
   
JIRA_PASSWORD = os.environ.get("JIRA_PASSWORD")  # from GitHub Secret

JIRA_PROJECT = os.environ.get("JIRA_PROJECT")                             # Changeable (e.g., "VAT", "XYZ", etc.)

AWS_REGION = "us-east-1"

SES_SENDER = "svallabhuni@vitechinc.com"                  # Must be verified in SES

SES_RECIPIENT = "svallabhuni@vitechinc.com"            # Must be verified in SES sandbox

EMAIL_SUBJECT = f"Jira Issue Report - {JIRA_PROJECT} Project"

EMAIL_BODY = f"Attached is the Jira issue report for the '{JIRA_PROJECT}' project."

# Output CSV filename based on project

CSV_FILENAME = f"jira_issues_{JIRA_PROJECT.lower()}.csv"

# -------------------- Jira Authentication --------------------

jira = JIRA(server=JIRA_URL, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))

# -------------------- JQL & Issue Fetching --------------------

jql_query = f"project = {JIRA_PROJECT}"

start_at = 0

max_results = 100

all_issues = []

print(f"📡 Fetching '{JIRA_PROJECT}' issues from Jira...\n")

while True:

    issues = jira.search_issues(jql_query, startAt=start_at, maxResults=max_results)

    if not issues:

        break

    all_issues.extend(issues)

    print(f"Fetched {len(all_issues)} issues so far...")

    start_at += max_results

print(f"\n✅ Done. Total issues fetched: {len(all_issues)}")

# -------------------- Export to CSV --------------------

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

    msg["Subject"] = EMAIL_SUBJECT

    msg["From"] = SES_SENDER

    msg["To"] = SES_RECIPIENT

    msg.attach(MIMEText(EMAIL_BODY, "plain"))

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
