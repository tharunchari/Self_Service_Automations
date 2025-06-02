import requests

import pandas as pd

import boto3

import time

from email.mime.multipart import MIMEMultipart

from email.mime.application import MIMEApplication

from email.mime.text import MIMEText

from botocore.exceptions import ClientError
 
# ===== CONFIGURATION =====

GITHUB_TOKEN = os.environ["GITHUB_PAT"]  # Replace with actual token

ENTERPRISE = "vitech"  # Replace with your GitHub enterprise name
 
AWS_REGION = "us-east-1"  # Replace as needed

SES_SENDER = "svallabhuni@vitechinc.com"  # Must be verified in SES

SES_RECIPIENT = "svallabhuni@vitechinc.com"  # Must be verified if SES is in sandbox
 
EMAIL_SUBJECT = "Filtered GitHub Enterprise Users Report"

EMAIL_BODY = "Attached is the filtered user list from GitHub Enterprise SCIM API."
 
HEADERS = {

    "Accept": "application/scim+json",

    "Authorization": f"Bearer {GITHUB_TOKEN}",

    "X-GitHub-Api-Version": "2022-11-28"

}
 
# ===== FETCH USERS =====

start_index = 1

count = 100

user_count = 0

user_data = []
 
while True:

    url = f"https://api.github.com/scim/v2/enterprises/{ENTERPRISE}/Users?startIndex={start_index}&count={count}"

    response = requests.get(url, headers=HEADERS)
 
    if response.status_code != 200:

        print(f"❌ Error: {response.status_code} - {response.text}")

        break
 
    data = response.json()

    users = data.get("Resources", [])

    if not users:

        break
 
    for user in users:

        display_name = user.get("displayName", "N/A")

        user_name = user.get("userName", "N/A")

        groups = [g.get("display", "Unknown Group") for g in user.get("groups", [])]

        group_list = ", ".join(groups) if groups else "No Groups"

        user_count += 1

        user_data.append([user_count, user_name, display_name, group_list])
 
    start_index += count

    time.sleep(0.5)
 
# ===== SAVE RAW DATA =====

df = pd.DataFrame(user_data, columns=["S.No", "Username", "Display Name", "Groups"])
 
# ===== FILTER DATA =====

df_filtered = df[df["Groups"] != "No Groups"]

df_filtered = df_filtered[~df_filtered["Username"].str.match(r"^[a-f0-9]{32,}")]

df_filtered.reset_index(drop=True, inplace=True)

df_filtered["S.No"] = df_filtered.index + 1
 
filtered_excel_filename = "GitHub_Enterprise_Users_Filtered.xlsx"

df_filtered.to_excel(filtered_excel_filename, index=False)
 
print(f"✅ Filtered data saved to {filtered_excel_filename}")

print(f"Total Users After Filtering: {len(df_filtered)}")
 
# ===== SEND VIA SES =====

try:

    ses = boto3.client("ses", region_name=AWS_REGION)
 
    msg = MIMEMultipart()

    msg["Subject"] = EMAIL_SUBJECT

    msg["From"] = SES_SENDER

    msg["To"] = SES_RECIPIENT
 
    msg.attach(MIMEText(EMAIL_BODY, "plain"))
 
    with open(filtered_excel_filename, "rb") as file:

        part = MIMEApplication(file.read())

        part.add_header("Content-Disposition", f"attachment; filename={filtered_excel_filename}")

        msg.attach(part)
 
    response = ses.send_raw_email(

        Source=SES_SENDER,

        Destinations=[SES_RECIPIENT],

        RawMessage={"Data": msg.as_string()}

    )

    print("📧 Email sent successfully via SES.")

except ClientError as e:

    print(f"❌ Failed to send email: {e}")

 
