import boto3
import requests
import os
from datetime import datetime, timezone
from dateutil import parser as date_parser

AWS_REGION = "us-east-1"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd"
GITHUB_ORG = "vitechsystems"
GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")  # Classic PAT from workflow env

# 🔹 New toggle
DRY_RUN = True  # Set to False to actually terminate instances

def send_sns_notification(subject, message):
    sns_client = boto3.client('sns', region_name=AWS_REGION)
    try:
        if DRY_RUN:
            print(f"[DRY-RUN] Would send SNS notification:\nSubject: {subject}\nMessage:\n{message}")
        else:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=message,
                Subject=subject
            )
            print("SNS notification sent successfully")
    except Exception as e:
        print("Failed to send SNS notification:", str(e))

def get_aws_self_hosted_instances():
    ec2_client = boto3.client('ec2', region_name=AWS_REGION)
    tag_key = 'Name'
    self_hosted_runner_value = 'Github_Self_Hosted_Runner'
    response = ec2_client.describe_instances(Filters=[
        {'Name': f'tag:{tag_key}', 'Values': [self_hosted_runner_value]},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ])
    instance_ids = []
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_ids.append(instance['InstanceId'])
    return instance_ids

def get_github_self_hosted_runners():
    runners = []
    page = 1
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    while True:
        url = f"https://api.github.com/orgs/{GITHUB_ORG}/actions/runners?per_page=100&page={page}"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        runners.extend(data["runners"])
        if len(data["runners"]) < 100:
            break
        page += 1
    return runners

def match_runners_with_instances(runners, instance_ids):
    matched = []
    for runner in runners:
        name = runner.get("name", "")
        for instance_id in instance_ids:
            if name.startswith(instance_id):
                matched.append((instance_id, runner))
    return matched

def get_idle_or_offline_runners(matched_runners):
    idle_or_offline = []
    now = datetime.now(timezone.utc)
    for instance_id, runner in matched_runners:
        status = runner.get("status")
        busy = runner.get("busy")
        runner_name = runner.get("name")
        runner_id = runner.get("id")
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        url = f"https://api.github.com/orgs/{GITHUB_ORG}/actions/runners/{runner_id}"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        runner_details = resp.json()

        # Try to find last seen timestamp
        last_seen_str = (
            runner_details.get("last_online_at")
            or runner_details.get("last_activity_at")
            or runner_details.get("created_at")
        )

        if not last_seen_str:
            print(f"⚠️ No timestamp found for runner {runner_name} ({instance_id}), skipping.")
            continue

        try:
            last_seen = date_parser.parse(last_seen_str)
        except Exception as e:
            print(f"⚠️ Failed to parse timestamp for runner {runner_name} ({instance_id}): {last_seen_str}, error={e}")
            continue

        idle_time = (now - last_seen).total_seconds()

        if status == "online" and busy:
            print(f"Skipping busy runner {runner_name} ({instance_id})")
            continue
        if runner_details.get("repositories") and len(runner_details["repositories"]) > 1:
            print(f"Skipping runner {runner_name} ({instance_id}) with multiple repo associations")
            continue
        if status == "online" and not busy and idle_time > 300:
            idle_or_offline.append((instance_id, runner_name, status, idle_time))
        elif status == "offline" and idle_time > 300:
            idle_or_offline.append((instance_id, runner_name, status, idle_time))
    return idle_or_offline


def terminate_instances(instance_ids):
    if DRY_RUN:
        print(f"[DRY-RUN] Would terminate instances: {instance_ids}")
        return
    ec2_client = boto3.client('ec2', region_name=AWS_REGION)
    try:
        ec2_client.terminate_instances(InstanceIds=instance_ids)
        print(f"Terminated instances: {instance_ids}")
    except Exception as e:
        print(f"Failed to terminate instances {instance_ids}: {str(e)}")

def main():
    if not GITHUB_TOKEN:
        raise Exception("CLASSIC_PAT environment variable not set.")
    print("Fetching AWS self-hosted runner instances...")
    aws_instance_ids = get_aws_self_hosted_instances()
    print(f"AWS EC2 Instance IDs: {aws_instance_ids}")

    print("Fetching GitHub self-hosted runners...")
    github_runners = get_github_self_hosted_runners()
    print(f"GitHub runners found: {len(github_runners)}")

    print("Matching runners with AWS instances...")
    matched_runners = match_runners_with_instances(github_runners, aws_instance_ids)
    print(f"Matched runners: {len(matched_runners)}")

    print("Filtering for idle/offline runners...")
    idle_or_offline = get_idle_or_offline_runners(matched_runners)
    print(f"Idle or offline runners for >5min: {idle_or_offline}")

    if idle_or_offline:
        terminated_ids = [item[0] for item in idle_or_offline]
        terminate_instances(terminated_ids)
        subject = "[DRY-RUN] Idle/Offline GitHub Self-Hosted Runners" if DRY_RUN else "Terminated Idle/Offline GitHub Self-Hosted Runners"
        message = "The following instances were flagged as idle or offline for more than 5 minutes:\n\n"
        for instance_id, runner_name, status, idle_time in idle_or_offline:
            message += (
                f"Instance ID: {instance_id}\n"
                f"Runner Name: {runner_name}\n"
                f"Status: {status}\n"
                f"Idle/Offline Time (seconds): {int(idle_time)}\n\n"
            )
        send_sns_notification(subject, message)
    else:
        print("No runners to terminate.")

if __name__ == "__main__":
    main()
