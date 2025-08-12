import boto3

import os

import requests

from datetime import datetime, timezone
 
# AWS Config

SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'

ec2_client = boto3.client('ec2')

sns_client = boto3.client('sns')
 
# GitHub Config

GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")  # Set this environment variable securely

ORG_NAME = "vitechsystems"

GITHUB_API_URL = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners"
 
# Condition: 7200 seconds = 2 hours

MIN_RUNNING_SECONDS = 7200
 
def get_ec2_instances():

    """Fetch running EC2 instances with tag 'Github_Self_Hosted_Runner' running > 2 hours."""

    instances_info = []

    response = ec2_client.describe_instances(

        Filters=[

            {'Name': 'instance-state-name', 'Values': ['running']},

            {'Name': 'tag:Name', 'Values': ['Github_Self_Hosted_Runner']}

        ]

    )

    for reservation in response['Reservations']:

        for instance in reservation['Instances']:

            launch_time = instance['LaunchTime']

            running_time = datetime.now(timezone.utc) - launch_time

            running_seconds = running_time.total_seconds()

            if running_seconds > MIN_RUNNING_SECONDS:

                instances_info.append({

                    "id": instance['InstanceId'],

                    "launch_time": launch_time,

                    "running_time": running_time

                })

    return instances_info
 
def get_github_online_runners():

    """Fetch GitHub runners online, grouped by idle (not busy) and busy, excluding Internal_Tools_Automation_Server."""

    headers = {

        "Accept": "application/vnd.github+json",

        "Authorization": f"Bearer {GITHUB_TOKEN}",

        "X-GitHub-Api-Version": "2022-11-28"

    }

    idle_runners = []

    busy_runners = []

    page = 1

    while True:

        resp = requests.get(f"{GITHUB_API_URL}?per_page=100&page={page}", headers=headers)

        resp.raise_for_status()

        data = resp.json()

        for runner in data.get("runners", []):

            name = runner.get("name", "")

            if name.startswith("Internal_Tools_Automation_Server"):

                continue

            if runner.get("status") == "online":

                if runner.get("busy"):

                    busy_runners.append(name)

                else:

                    idle_runners.append(name)

        if "next" not in resp.links:

            break

        page += 1

    return idle_runners, busy_runners
 
def send_sns_notification(subject, message):

    """Send SNS notification."""

    sns_client.publish(

        TopicArn=SNS_TOPIC_ARN,

        Message=message,

        Subject=subject

    )
 
def main():

    instances = get_ec2_instances()

    idle_runners, busy_runners = get_github_online_runners()
 
    message_lines = ["=== EC2 Self-Hosted Runners (> 2 hours) ==="]

    if instances:

        for inst in instances:

            message_lines.append(f"Instance ID: {inst['id']}")

            message_lines.append(f"Launch Time: {inst['launch_time']}")

            message_lines.append(f"Running Time: {inst['running_time']}\n")

    else:

        message_lines.append("No EC2 instances found.")
 
    message_lines.append("\n=== GitHub Runners (Online & Idle) ===")

    if idle_runners:

        for runner in idle_runners:

            message_lines.append(runner)

    else:

        message_lines.append("No idle runners found.")
 
    message_lines.append("\n=== GitHub Runners (Online & Busy) ===")

    if busy_runners:

        for runner in busy_runners:

            message_lines.append(runner)

    else:

        message_lines.append("No busy runners found.")
 
    # Optionally extract instance-like IDs from runner names

    github_idle_ids = [r.split("-")[0] for r in idle_runners]

    github_busy_ids = [r.split("-")[0] for r in busy_runners]
 
    message_lines.append("\nGitHub Runners (Idle) IDs only:")

    if github_idle_ids:

        for rid in github_idle_ids:

            message_lines.append(rid)

    else:

        message_lines.append("No idle runner IDs found.")
 
    message_lines.append("\nGitHub Runners (Busy) IDs only:")

    if github_busy_ids:

        for rid in github_busy_ids:

            message_lines.append(rid)

    else:

        message_lines.append("No busy runner IDs found.")
 
    message_text = "\n".join(message_lines)
 
    # Print message locally for debugging

    print(message_text)
 
    # Send SNS notification only if we have any data

    if instances or idle_runners or busy_runners:

        send_sns_notification("EC2 & GitHub Runners Report", message_text)
 
if __name__ == "__main__":

    main()

 
