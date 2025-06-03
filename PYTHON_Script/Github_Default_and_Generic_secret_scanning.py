import requests
import pandas as pd
import os
import time
import boto3
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText

# ---------------------- CONFIGURATION ----------------------
GITHUB_TOKEN = os.environ["CLASSIC_PAT"]
ORG_NAME = "vitechsystems"
SENDER = "svallabhuni@vitechinc.com"  # Must be verified in AWS SES
RECIPIENT = "svallabhuni@vitechinc.com"  # Must be verified if SES sandbox
AWS_REGION = "us-east-1"

INCLUDE_TYPES = {
    "generic_credential", "password", "rsa_private_key",
    "http_bearer_authentication_header", "http_basic_authentication_header",
    "mongodb_connection_string", "mysql_connection_string", "openssh_private_key",
    "pgp_private_key", "postgres_connection_string"
}

PER_PAGE = 100
HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28"
}

# ---------------------- FETCH REPOS ----------------------
def fetch_repositories():
    all_repos = []
    params = {"per_page": PER_PAGE, "page": 1}
    while True:
        print(f"Fetching repos page {params['page']}...")
        response = requests.get(f"https://api.github.com/orgs/{ORG_NAME}/repos", headers=HEADERS, params=params)
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.text}")
            break
        repos = response.json()
        if not repos:
            break
        all_repos.extend(repos)
        params["page"] += 1
        time.sleep(0.2)
    print(f"✅ Total repositories fetched: {len(all_repos)}")
    return all_repos

# ---------------------- FETCH ALERTS ----------------------
def fetch_alerts(repos, filter_types=False):
    all_alerts = []
    for repo in repos:
        full_name = repo["full_name"]
        repo_name = repo["name"]
        owner = repo["owner"]["login"]
        print(f"🔍 Fetching alerts for {full_name}...")
        page = 1
        while True:
            params = {"per_page": PER_PAGE, "page": page}
            if filter_types:
                params["secret_type"] = ",".join(INCLUDE_TYPES)
            url = f"https://api.github.com/repos/{owner}/{repo_name}/secret-scanning/alerts"
            res = requests.get(url, headers=HEADERS, params=params)
            if res.status_code == 404:
                print(f"⚠️  Secret scanning not enabled for {full_name}. Skipping...")
                break
            elif res.status_code == 403:
                print("⏳ Rate limit hit. Waiting...")
                time.sleep(60)
                continue
            elif res.status_code != 200:
                print(f"❌ Error: {res.status_code}")
                break
            alerts = res.json()
            if not alerts:
                break
            for alert in alerts:
                all_alerts.append({
                    "full_name": full_name,
                    "alert_number": alert["number"],
                    "secret_type": alert["secret_type"],
                    "state": alert["state"],
                    "created_at": alert["created_at"],
                    "resolved_at": alert.get("resolved_at", ""),
                    "secret": alert.get("secret", ""),
                    "login": owner
                })
            page += 1
            time.sleep(0.2)
    return all_alerts

# ---------------------- SAVE TO EXCEL ----------------------
def save_alerts_to_excel(alerts, filename):
    if alerts:
        df = pd.DataFrame(alerts)
        df.to_excel(filename, index=False)
        print(f"✅ Saved {len(df)} alerts to {filename}")
    else:
        print(f"❌ No alerts to save for {filename}")

# ---------------------- SEND EMAIL VIA SES ----------------------
def send_email_with_attachments(files, subject, body):
    client = boto3.client("ses", region_name=AWS_REGION)
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(body, "plain"))
    for file_path in files:
        with open(file_path, "rb") as f:
            part = MIMEApplication(f.read())
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(file_path))
            msg.attach(part)
    response = client.send_raw_email(Source=SENDER, Destinations=[RECIPIENT], RawMessage={"Data": msg.as_string()})
    print(f"📧 Email sent! Message ID: {response['MessageId']}")

# ---------------------- MAIN ----------------------
if __name__ == "__main__":
    all_repos = fetch_repositories()

    # Fetch all alerts
    all_alerts = fetch_alerts(all_repos, filter_types=False)
    all_alerts_file = "All_scanning_alerts.xlsx"
    save_alerts_to_excel(all_alerts, all_alerts_file)

    # Fetch only generic/selected alerts
    generic_alerts = fetch_alerts(all_repos, filter_types=True)
    generic_alerts_file = "Generic_secret_scanning_alerts.xlsx"
    save_alerts_to_excel(generic_alerts, generic_alerts_file)

    # Send email
    send_email_with_attachments(
        [all_alerts_file, generic_alerts_file],
        subject="GitHub Secret Scanning Reports",
        body="Please find attached the GitHub secret scanning alerts reports (all & generic types)."
    )
