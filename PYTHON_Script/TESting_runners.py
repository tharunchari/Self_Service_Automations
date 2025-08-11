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
 
# === EC2 Tagging ===

def mark_instance_as_notified(instance_id):

    ec2_client = boto3.client('ec2', region_name=AWS_REGION)

    try:

        ec2_client.create_tags(Resources=[instance_id], Tags=[{'Key': 'Notified', 'Value': 'True'}])

        print(f"Instance {instance_id} marked as notified")

    except Exception as e:

        print(f"Failed to tag instance {instance_id}: {str(e)}")
 
# === EC2 Self-Hosted Runner Check ===

def check_self_hosted_runners():

    ec2_client = boto3.client('ec2', region_name=AWS_REGION)

    tag_key = 'Name'

    self_hosted_runner_value = 'Github_Self_Hosted_Runner'

    current_time = datetime.now(timezone.utc)

    self_hosted_instances = []
 
    response = ec2_client.describe_instances(Filters=[

        {'Name': f'tag:{tag_key}', 'Values': [self_hosted_runner_value]},

        {'Name': 'instance-state-name', 'Values': ['running']}

    ])
 
    for reservation in response['Reservations']:

        for instance in reservation['Instances']:

            instance_id = instance['InstanceId']

            launch_time = instance['LaunchTime']

            running_time = current_time - launch_time

            notified_tag = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Notified'), None)
 
            if running_time.total_seconds() > 7200:  # > 2 hours

                self_hosted_instances.append({

                    'Instance ID': instance_id,

                    'Launch Time': launch_time,

                    'Running Time': running_time

                })

                if not notified_tag:

                    mark_instance_as_notified(instance_id)
 
    return self_hosted_instances
 
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

    # 1. Check EC2 Runners > 2 Hours

    ec2_runners = check_self_hosted_runners()
 
    # 2. Get GitHub Runners (Online & Idle)

    github_runners = get_online_idle_github_runners()
 
    # 3. Build Notification Message

    message_parts = []
 
    if ec2_runners:

        message_parts.append("=== EC2 Self-Hosted Runners > 2 Hours ===")

        for inst in ec2_runners:

            message_parts.append(f"Instance ID: {inst['Instance ID']}")

            message_parts.append(f"Launch Time: {inst['Launch Time']}")

            message_parts.append(f"Running Time: {inst['Running Time']}")

            message_parts.append("")

    else:

        message_parts.append("No EC2 self-hosted runners running > 2 hours.")
 
    if github_runners:

        message_parts.append("=== GitHub Runners (Online & Idle) ===")

        for runner in github_runners:

            message_parts.append(runner)

    else:

        message_parts.append("No GitHub runners online & idle.")
 
    # 4. Send SNS Notification

    send_sns_notification(

        "Self-Hosted Runner Report",

        "\n".join(message_parts)

    )

 
