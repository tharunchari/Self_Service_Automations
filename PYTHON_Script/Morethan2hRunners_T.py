import boto3
import os
import requests
import re
from datetime import datetime, timezone
 
# === AWS Config ===
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd'
#SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'
ec2_client = boto3.client('ec2')
sns_client = boto3.client('sns')
 
# === GitHub Config ===
GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")  # Ensure this is set in your environment
ORG_NAME = "vitechsystems"
GITHUB_API_URL = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners"
 
# Condition: 7200 seconds (2 hours)
MIN_RUNNING_SECONDS = 1800
 
 
def get_ec2_instances():
    """Fetch running EC2 instances with tag 'Github_Self_Hosted_Runner' running longer than threshold."""
    instances_info = []
 
    response = ec2_client.describe_instances(
        Filters=[
            {'Name': 'instance-state-name', 'Values': ['running']},
            {'Name': 'tag:Name', 'Values': ['Github_Self_Hosted_Runner']}
        ]
    )
 
    for reservation in response.get('Reservations', []):
        for instance in reservation.get('Instances', []):
            launch_time = instance['LaunchTime']
            running_time = datetime.now(timezone.utc) - launch_time
 
            if running_time.total_seconds() > MIN_RUNNING_SECONDS:
                instances_info.append({
                    "id": instance['InstanceId'],
                    "launch_time": launch_time,
                    "running_time": running_time,
                    "tags": {t['Key']: t['Value'] for t in instance.get('Tags', [])}
                })
 
    return instances_info
 
 
def get_github_idle_runners():
    """Fetch GitHub runners that are online and idle."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
 
    runners_info = []
    page = 1
 
    while True:
        resp = requests.get(f"{GITHUB_API_URL}?per_page=100&page={page}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
 
        for runner in data.get("runners", []):
            if runner.get("status") == "online" and not runner.get("busy"):
                runners_info.append(runner["name"])
 
        if "next" not in resp.links:
            break
 
        page += 1
 
    return runners_info
 
 
def send_sns_notification(subject, message):
    """Send SNS notification."""
    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Message=message,
        Subject=subject
    )
 
 
def main():
    instances = get_ec2_instances()
    ec2_ids = [inst["id"] for inst in instances]
    github_idle_runners = get_github_idle_runners()
 
    # Extract IDs from runner names using regex
    instance_id_pattern = re.compile(r'i-[0-9a-fA-F]+')
    github_idle_ids = [
        (match.group(0) if (match := instance_id_pattern.search(runner)) else runner)
        for runner in github_idle_runners
    ]
 
    # Compute intersection and difference
    matched_ids = list(set(ec2_ids) & set(github_idle_ids))
    not_matched_ids = list(set(ec2_ids) - set(github_idle_ids))
 
    if instances and (matched_ids or not_matched_ids):
        to_terminate = matched_ids + not_matched_ids
 
        # Terminate instances
        ec2_client.terminate_instances(InstanceIds=to_terminate)
 
        # === Build clean termination report ===
        message_lines = ["Self-hosted GitHub runners running for more than 30 min have been terminated.\n"]
 
        for inst in instances:
            if inst['id'] in to_terminate:
                message_lines.append(f"Instance ID: {inst['id']}")
                message_lines.append(f"Launch Time: {inst['launch_time']}")
                message_lines.append(f"Running Time: {inst['running_time']}\n")
 
        report_text = "\n".join(message_lines)
 
        # Print and send SNS
        print(report_text)
        send_sns_notification("Self-hosted GitHub runners running for more than 30 min have been terminated.", report_text)
 
    else:
        print("No EC2 instances above threshold. Skipping SNS and termination operations.")
 
 
if __name__ == "__main__":
    main()
