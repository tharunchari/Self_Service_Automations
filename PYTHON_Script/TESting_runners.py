import boto3

import os

import requests

from datetime import datetime, timezone
 
# === Config ===

AWS_REGION = "us-east-1"

GITHUB_TOKEN = os.environ["CLASSIC_PAT"]  # GitHub Personal Access Token

ORG_NAME = "vitechsystems"  # Your GitHub Org Name

SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:Testing"
 
# === SNS Notification ===

def send_sns_notification(subject, message):

    sns_client = boto3.client('sns', region_name=AWS_REGION)

    try:

        sns_client.publish(

            TopicArn=SNS_TOPIC_ARN,

            Message=message,

            Subject=subject

        )

        print("SNS notification sent successfully")

    except Exception as e:

        print("Failed to send SNS notification:", str(e))
 
# === GitHub Runner API Check ===

def get_online_idle_github_runners():

    url = f"https://api.github.com/orgs/{ORG_NAME}/actions/runners?per_page=100"

    headers = {

        "Accept": "application/vnd.github+json",

        "Authorization": f"Bearer {GITHUB_TOKEN}",

        "X-GitHub-Api-Version": "2022-11-28"

    }

    runners_list = []

    try:

        response = requests.get(url, headers=headers)

        response.raise_for_status()

        data = response.json()
 
        for runner in data.get("runners", []):

            if runner.get("status") == "online" and runner.get("busy") == True:

                runners_list.append(runner["name"])

    except Exception as e:

        print("Error fetching GitHub runners:", str(e))
 
    return runners_list
 
# === Main Function ===

if __name__ == "__main__":

    current_time = datetime.now(timezone.utc)
 
    # Get all EC2 self-hosted runners (running)

    ec2_client = boto3.client('ec2', region_name=AWS_REGION)

    response = ec2_client.describe_instances(Filters=[

        {'Name': 'tag:Name', 'Values': ['Github_Self_Hosted_Runner']},

        {'Name': 'instance-state-name', 'Values': ['running']}

    ])
 
    ec2_runners_full = []

    ec2_runners_over_2h = []

    for reservation in response['Reservations']:

        for instance in reservation['Instances']:

            instance_id = instance['InstanceId']

            launch_time = instance['LaunchTime']

            running_time = current_time - launch_time

            ec2_runners_full.append({

                'Instance ID': instance_id,

                'Launch Time': launch_time,

                'Running Time': running_time

            })

            if running_time.total_seconds() > 500:  # > 2 hours

                ec2_runners_over_2h.append(instance_id)
 
    # GitHub Runners (Online & Idle)

    github_runners = get_online_idle_github_runners()
 
    # === Build Notification ===

    message_parts = []
 
    # Section 1: All EC2 runners

    message_parts.append("=== EC2 Self-Hosted Runners > 2 Hours ===")

    for inst in ec2_runners_full:

        message_parts.append(f"Instance ID: {inst['Instance ID']}")

        message_parts.append(f"Launch Time: {inst['Launch Time']}")

        message_parts.append(f"Running Time: {inst['Running Time']}")

    message_parts.append("")
 
    # Section 2: GitHub runners

    message_parts.append("=== GitHub Runners (Online & Idle) ===")

    for runner in github_runners:

        message_parts.append(runner)

    message_parts.append("")
 
    # Section 3: Summary

    message_parts.append("EC2 Self-Hosted Runners > 2 Hours")

    for inst_id in ec2_runners_over_2h:

        message_parts.append(inst_id)
 
    message_parts.append("GitHub Runners (Online & Idle)")

    for runner in github_runners:

        runner_id = runner.split("-")[0]  # Trim after first dash

        message_parts.append(runner_id)
 
    # Send SNS

    send_sns_notification(

        "Self-Hosted Runner Report",

        "\n".join(message_parts)

    )

 
