import boto3

import os

import requests

from datetime import datetime, timezone
 
# === Config ===

SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'

GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")  # set your GitHub PAT in env var

ORG_NAME = "vitechsystems"

MIN_RUNNING_SECONDS = 700  # 2 hours
 
ec2_client = boto3.client('ec2')

sns_client = boto3.client('sns')
 
def get_ec2_instances():

    """Fetch running EC2 instances with tag 'Github_Self_Hosted_Runner'."""

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
 
def get_github_runners():

    """Fetch GitHub organization runners that are online & idle."""

    url = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners?per_page=100"

    headers = {

        "Accept": "application/vnd.github+json",

        "Authorization": f"Bearer {GITHUB_TOKEN}",

        "X-GitHub-Api-Version": "2022-11-28"

    }

    runners_full = []

    runners_ids_only = []
 
    response = requests.get(url, headers=headers)

    response.raise_for_status()

    data = response.json()
 
    for runner in data.get("runners", []):

        if runner.get("status") == "online" and runner.get("busy", False):

            name = runner.get("name", "")

            runners_full.append(name)

            if "-" in name:

                runners_ids_only.append(name.split("-")[0])
 
    return runners_full, runners_ids_only
 
def send_sns_notification(subject, message):

    """Send SNS notification."""

    sns_client.publish(

        TopicArn=SNS_TOPIC_ARN,

        Message=message,

        Subject=subject

    )
 
def main():

    instances = get_ec2_instances()

    gh_full, gh_ids = get_github_runners()
 
    # Prepare message text

    message_lines = ["=== EC2 Self-Hosted Runners (> 2 hours) ==="]

    for inst in instances:

        message_lines.append(f"Instance ID: {inst['id']}")

        message_lines.append(f"Launch Time: {inst['launch_time']}")

        message_lines.append(f"Running Time: {inst['running_time']}\n")
 
    message_lines.append("\nSNS notification sent with these EC2 IDs:")

    for inst in instances:

        message_lines.append(inst['id'])
 
    message_lines.append("\n=== GitHub Runners (Online & Idle) ===")

    message_lines.extend(gh_full)
 
    message_lines.append("\nGitHub Runners (Online & Idle) IDs only:")

    message_lines.extend(gh_ids)
 
    message_text = "\n".join(message_lines)
 
    # Print locally

    print(message_text)
 
    # Send SNS

    if instances or gh_full:

        send_sns_notification("EC2 & GitHub Runners Report", message_text)
 
if __name__ == "__main__":

    main()

 
