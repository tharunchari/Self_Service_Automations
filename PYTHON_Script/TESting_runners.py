import boto3
import os
import requests
 
# === Config ===
AWS_REGION = "us-east-1"
GITHUB_TOKEN = os.environ["CLASSIC_PAT"])  # Or hardcode for testing
ORG_NAME = "vitechsystems"
 
if not GITHUB_TOKEN:
    raise ValueError("Please set the GITHUB_TOKEN environment variable")
 
# 1️⃣ Get list of EC2 instance IDs
ec2_client = boto3.client("ec2", region_name=AWS_REGION)
instances = ec2_client.describe_instances()
instance_ids = [
    inst["InstanceId"]
    for res in instances["Reservations"]
    for inst in res["Instances"]
]
print("EC2 Instances found:")
for iid in instance_ids:
    print(iid)
print("====================")
 
# 2️⃣ Get runners from GitHub
url = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners?per_page=100"
headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28"
}
 
response = requests.get(url, headers=headers)
response.raise_for_status()
runners = response.json().get("runners", [])
 
# 3️⃣ Filter runners
matching_ids = []
for runner in runners:
    name = runner.get("name", "")
    status = runner.get("status")
    busy = runner.get("busy", True)
 
    # Filter conditions
    if (
        status == "online"
        and busy is False
        and not name.startswith("Internal_Tools_Automation_Server")
    ):
        # Match against EC2 instance IDs
        for iid in instance_ids:
            if name.startswith(iid):
                # Extract first two parts of name (split by "-")
                short_id = "-".join(name.split("-")[0:2])
                matching_ids.append(short_id)
 
# 4️⃣ Print matching instance IDs
print("Matching EC2 Instance IDs with GitHub runners:")
for mid in matching_ids:
    print(mid)
