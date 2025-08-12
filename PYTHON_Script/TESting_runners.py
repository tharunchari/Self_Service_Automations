import boto3

import os

import requests

from datetime import datetime, timezone

import re
 
# === Config ===

AWS_REGION = "us-east-1"

SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:Testing"

GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")

ORG_NAME = "vitechsystems"
 
if not GITHUB_TOKEN:

    raise ValueError("Please set the CLASSIC_PAT environment variable")
 
# === Get EC2 instances ===

def get_long_running_ec2_runners():

    ec2_client = boto3.client("ec2", region_name=AWS_REGION)

    now = datetime.now(timezone.utc)

    long_running = []
 
    instances = ec2_client.describe_instances(

        Filters=[

            {"Name": "tag:Name", "Values": ["*runner*"]},

            {"Name": "instance-state-name", "Values": ["running"]}

        ]

    )
 
    for reservation in instances["Reservations"]:

        for instance in reservation["Instances"]:

            launch_time = instance["LaunchTime"]

            running_time = now - launch_time

            if running_time.total_seconds() > 500:  # > 2 hours

                long_running.append({

                    "InstanceId": instance["InstanceId"],

                    "LaunchTime": launch_time,

                    "RunningTime": running_time

                })

    return long_running
 
# === Get GitHub online & idle runners ===

def get_github_online_idle_runners():

    url = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners?per_page=100"

    headers = {

        "Accept": "application/vnd.github+json",

        "Authorization": f"Bearer {GITHUB_TOKEN}",

        "X-GitHub-Api-Version": "2022-11-28"

    }

    runners = []

    page = 1
 
    while True:

        resp = requests.get(f"{url}&page={page}", headers=headers)

        resp.raise_for_status()

        data = resp.json()
 
        for runner in data.get("runners", []):

            if runner["status"] == "online" and runner["busy"] is True:

                runners.append(runner["name"])
 
        if "next" not in resp.links:

            break

        page += 1
 
    return runners
 
# === Send SNS notification ===

def send_sns_notification(subject, message):

    sns_client = boto3.client("sns", region_name=AWS_REGION)

    sns_client.publish(

        TopicArn=SNS_TOPIC_ARN,

        Message=message,

        Subject=subject

    )

    print("SNS notification sent successfully")
 
# === Main ===

def main():

    long_running_ec2 = get_long_running_ec2_runners()

    github_idle_runners = get_github_online_idle_runners()
 
    # Extract EC2 IDs from GitHub runner names

    github_idle_ids = {

        re.match(r"(i-[0-9a-fA-F]+)", runner).group(1)

        for runner in github_idle_runners

        if re.match(r"(i-[0-9a-fA-F]+)", runner)

    }
 
    message_lines = []
 
    # EC2 > 2 hours

    message_lines.append("=== EC2 Self-Hosted Runners (> 2 hours) ===")

    if long_running_ec2:

        for inst in long_running_ec2:

            message_lines.append(f"Instance ID: {inst['InstanceId']}")

            message_lines.append(f"Launch Time: {inst['LaunchTime']}")

            message_lines.append(f"Running Time: {inst['RunningTime']}")

    else:

        message_lines.append("No long-running EC2 runners found.")
 
    # GitHub Online & Idle

    message_lines.append("\n=== GitHub Runners (Online & Idle) ===")

    if github_idle_runners:

        for runner in github_idle_runners:

            message_lines.append(runner)

    else:

        message_lines.append("No online & idle GitHub runners found.")
 
    # GitHub IDs only

    message_lines.append("\nGitHub Runners (Online & Idle) id's")

    if github_idle_ids:

        for rid in sorted(github_idle_ids):

            message_lines.append(rid)

    else:

        message_lines.append("No GitHub runner IDs extracted.")
 
    final_message = "\n".join(message_lines)

    print(final_message)

    send_sns_notification("Runner Status", final_message)
 
if __name__ == "__main__":

    main()

 
