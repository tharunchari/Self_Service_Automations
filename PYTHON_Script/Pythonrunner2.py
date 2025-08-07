import boto3

import os

import requests

from datetime import datetime, timezone
 
GITHUB_TOKEN = os.environ["CLASSIC_PAT"]
 
def send_sns_notification(subject, message):

    sns_client = boto3.client('sns')

    topic_arn = 'arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd'

    try:

        sns_client.publish(

            TopicArn=topic_arn,

            Message=message,

            Subject=subject

        )

        print("SNS notification sent successfully")

    except Exception as e:

        print("Failed to send SNS notification:", str(e))
 
def mark_instance_as_notified(instance_id):

    ec2_client = boto3.client('ec2')

    try:

        ec2_client.create_tags(

            Resources=[instance_id],

            Tags=[{'Key': 'Notified', 'Value': 'True'}]

        )

        print(f"Instance {instance_id} marked as notified")

    except Exception as e:

        print(f"Failed to tag instance {instance_id}: {str(e)}")
 
def get_idle_github_runner_ids():

    url = "https://api.github.com/orgs/vitechsystems/actions/runners?per_page=100"

    headers = {

        "Accept": "application/vnd.github+json",

        "Authorization": f"Bearer {GITHUB_TOKEN}",

        "X-GitHub-Api-Version": "2022-11-28"

    }

    try:

        response = requests.get(url, headers=headers)

        response.raise_for_status()

        data = response.json()

        idle_runners = [

            runner['name'].split('-')[0] + "-" + runner['name'].split('-')[1]

            for runner in data.get("runners", [])

            if runner['status'] == "online" and not runner['busy']

        ]

        return idle_runners

    except requests.RequestException as e:

        print("Failed to fetch GitHub runners:", str(e))

        return []
 
def check_running_ec2_instances():

    ec2_client = boto3.client('ec2')

    tag_key = 'Name'

    self_hosted_runner_value = 'Github_Self_Hosted_Runner'

    bam_ip_value = 'bam::ip*'

    current_time = datetime.now(timezone.utc)

    self_hosted_instances = []

    bam_ip_instances = []
 
    # --- Self-Hosted GitHub Runners ---

    response_self_hosted = ec2_client.describe_instances(Filters=[

        {'Name': f'tag:{tag_key}', 'Values': [self_hosted_runner_value]},

        {'Name': 'instance-state-name', 'Values': ['running']}

    ])

    for reservation in response_self_hosted['Reservations']:

        for instance in reservation['Instances']:

            instance_id = instance['InstanceId']

            launch_time = instance['LaunchTime']

            running_time = current_time - launch_time

            notified_tag = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Notified'), None)

            if running_time.total_seconds() > 7200 and not notified_tag:

                self_hosted_instances.append({

                    'Instance ID': instance_id,

                    'Launch Time': launch_time,

                    'Running Time': running_time

                })

                mark_instance_as_notified(instance_id)
 
    # --- Bamboo IP Instances ---

    response_bam_ip = ec2_client.describe_instances(Filters=[

        {'Name': f'tag:{tag_key}', 'Values': [bam_ip_value]},

        {'Name': 'instance-state-name', 'Values': ['running']}

    ])

    for reservation in response_bam_ip['Reservations']:

        for instance in reservation['Instances']:

            instance_id = instance['InstanceId']

            launch_time = instance['LaunchTime']

            running_time = current_time - launch_time

            notified_tag = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Notified'), None)

            if running_time.total_seconds() > 10800 and not notified_tag:

                bam_ip_instances.append({

                    'Instance ID': instance_id,

                    'Launch Time': launch_time,

                    'Running Time': running_time

                })

                mark_instance_as_notified(instance_id)
 
    # --- SNS Message ---

    final_message = ""

    if self_hosted_instances:

        final_message += "Self-Hosted Github Runners running more than 2 hours:\n"

        for inst in self_hosted_instances:

            final_message += f"Instance ID: {inst['Instance ID']}\n"

            final_message += f"Launch Time: {inst['Launch Time']}\n"

            final_message += f"Running Time: {inst['Running Time']}\n\n"
 
    if bam_ip_instances:

        final_message += "Bamboo Elastic Instances running more than 3 hours:\n"

        for inst in bam_ip_instances:

            final_message += f"Instance ID: {inst['Instance ID']}\n"

            final_message += f"Launch Time: {inst['Launch Time']}\n"

            final_message += f"Running Time: {inst['Running Time']}\n\n"
 
    if final_message:

        send_sns_notification(

            "(Testing Mail) Self-Hosted Runners (2+ hrs) & Bamboo Elastic Instances (3+ hrs) Running",

            final_message

        )

    else:

        print("No instances exceeding thresholds.")
 
    # --- Shutdown Logic ---

    runner_instance_ids = [inst['Instance ID'] for inst in self_hosted_instances]

    idle_runner_names = get_idle_github_runner_ids()
 
    to_shutdown = [iid for iid in runner_instance_ids if iid in idle_runner_names]

    if to_shutdown:

        for iid in to_shutdown:

            try:

                ec2_client.stop_instances(InstanceIds=[iid])

                print(f"Stopping idle GitHub runner instance: {iid}")

            except Exception as e:

                print(f"Error stopping instance {iid}: {e}")

    else:

        print("No idle GitHub runner instances to stop.")
 
# Entry point

check_running_ec2_instances()

 
