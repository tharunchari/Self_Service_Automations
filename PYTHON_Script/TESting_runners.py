import boto3
import os
import requests
import re
from datetime import datetime, timezone
 
# === AWS Config ===
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'
ec2_client = boto3.client('ec2')
sns_client = boto3.client('sns')
 
# === GitHub Config ===
GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")  # Ensure this is set in your environment
ORG_NAME = "vitechsystems"
GITHUB_API_URL = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners"
 
# Condition: 7200 seconds (2 hours)
MIN_RUNNING_SECONDS = 7200
 
 
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
 
    # === Build the message text ===
    if instances:
        message_lines = ["=== EC2 Self-Hosted Runners (> threshold) ==="]
 
        for inst in instances:
            message_lines.append(f"Instance ID: {inst['id']}")
            message_lines.append(f"Launch Time: {inst['launch_time']}")
            message_lines.append(f"Running Time: {inst['running_time']}")
            message_lines.append(f"Tags: {inst['tags']}\n")
 
        message_lines.append("SNS notification sent with these EC2 IDs:")
        message_lines.extend(ec2_ids)
 
        message_lines.append("\n=== GitHub Runners (Online & Idle) ===")
        message_lines.extend(github_idle_runners)
 
        message_lines.append("\nGitHub Runners (Online & Idle) IDs only:")
        message_lines.extend(github_idle_ids)
 
        message_lines.append("\nMatched EC2 IDs and GitHub Runners (to be terminated)")
        message_lines.extend(matched_ids)
 
        message_lines.append("\nNot Matched EC2 IDs (to be terminated)")
        message_lines.extend(not_matched_ids)
 
        message_text = "\n".join(message_lines)
 
        print(message_text)
 
        # Send SNS notification only when EC2 > threshold exist
        send_sns_notification("EC2 & GitHub Runners Termination Report", message_text)
 
        # Terminate instances only when EC2 > threshold exist
        if matched_ids or not_matched_ids:
            print("\nEligible EC2 instances to terminate (tagged 'Github_Self_Hosted_Runner'):")
 
            for inst in instances:
                print(f"  - {inst['id']} (Tags: {inst['tags']})")
 
            to_terminate = matched_ids + not_matched_ids
 
            print(f"\nTerminating EC2 instances now: {to_terminate}")
            ec2_client.terminate_instances(InstanceIds=to_terminate)
 
            # Show confirmation
            for inst_id in matched_ids:
                print(f"✅ Terminated matched instance: {inst_id}")
 
            for inst_id in not_matched_ids:
                print(f"✅ Terminated not matched instance: {inst_id}")
 
    else:
        print("No EC2 instances above threshold. Skipping SNS and termination operations.")
 
 
if __name__ == "__main__":
    main()
