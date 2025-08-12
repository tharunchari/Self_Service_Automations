import boto3

import os

import requests

from datetime import datetime, timezone
 
# AWS Config

SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'

ec2_client = boto3.client('ec2')

sns_client = boto3.client('sns')
 
# GitHub Config

GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")  # Set in env vars

ORG_NAME = "vitechsystems"

GITHUB_API_URL = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners"
 
# Condition: 7200 seconds = 2 hours

MIN_RUNNING_SECONDS = 700
 
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

    github_idle_runners = get_github_idle_runners()
 
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

    for runner in github_idle_runners:

        message_lines.append(runner)
 
    # Extract just the EC2 instance IDs from GitHub runner names

    github_idle_ids = [runner.split("-")[0] for runner in github_idle_runners]
 
    message_lines.append("\nGitHub Runners (Online & Idle) id's ")

    for rid in github_idle_ids:

        message_lines.append(rid)
 
    message_text = "\n".join(message_lines)
 
    # Print locally

    print(message_text)
 
    # Send SNS

    if instances or github_idle_runners:

        send_sns_notification("EC2 & GitHub Runners Report", message_text)
 
if __name__ == "__main__":

    main()

 
