import boto3
import requests
from datetime import datetime, timedelta, timezone
import os
from dateutil import parser as date_parser

# ---------- CONFIG ----------
AWS_REGION = "us-east-1"
INSTANCE_NAME_TAG = "GitHub-Runner"
GITHUB_ORG = "vitechsystems"
GITHUB_TOKEN = "your_github_token"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd:70782ed6-97d0-4386-95c0-2e5890ec5c37"
DRY_RUN = True   # <<--- Set to False to actually terminate
# ----------------------------

ec2 = boto3.client("ec2", region_name=AWS_REGION)
sns = boto3.client("sns", region_name=AWS_REGION)

def get_old_instances():
    """Get EC2 instances with Name tag = INSTANCE_NAME_TAG and older than 60 mins"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=60)

    resp = ec2.describe_instances(
        Filters=[{"Name": "tag:Name", "Values": [INSTANCE_NAME_TAG]}]
    )

    instances = []
    for r in resp["Reservations"]:
        for i in r["Instances"]:
            launch_time = i["LaunchTime"]
            if launch_time < cutoff:
                instances.append(i["InstanceId"])
    return instances

def get_github_runners():
    """Get all GitHub org runners"""
    url = f"https://api.github.com/orgs/{GITHUB_ORG}/actions/runners"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    runners = []
    while url:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        runners.extend(data.get("runners", []))
        url = None
        if "next" in resp.links:
            url = resp.links["next"]["url"]
    return runners

def terminate_and_notify(instances):
    """Terminate instances and send SNS"""
    if not instances:
        print("No instances to terminate.")
        return

    if DRY_RUN:
        print(f"[Dry Run] Would terminate: {instances}")
        return

    print(f"Terminating: {instances}")
    ec2.terminate_instances(InstanceIds=instances)

    message = f"Terminated AWS instances: {instances}"
    sns.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="GitHub Runner Cleanup")

def main():
    print("Fetching AWS instances...")
    aws_instances = get_old_instances()
    print(f"AWS instances: {aws_instances}")

    print("Fetching GitHub runners...")
    gh_runners = get_github_runners()
    print(f"GitHub runners found: {len(gh_runners)}")

    # Match by instance ID in runner name
    matched = {r["name"]: r for r in gh_runners if r["name"] in aws_instances}

    # Find runners not online & busy
    bad_instances = []
    for name, runner in matched.items():
        status = runner["status"].lower()
        busy = runner.get("busy", False)
        print(f"Runner {name} → Status: {status}, Busy: {busy}")
        if status != "online" or not busy:
            bad_instances.append(name)

    print(f"Instances to terminate: {bad_instances}")
    terminate_and_notify(bad_instances)

if __name__ == "__main__":
    main()
