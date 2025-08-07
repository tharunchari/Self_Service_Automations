import boto3
from datetime import datetime, timezone
import subprocess  # For calling your existing curl + jq
 
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
 
def check_running_ec2_instances():
    ec2_client = boto3.client('ec2')
    tag_key = 'Name'
    self_hosted_runner_value = 'Github_Self_Hosted_Runner'
    bam_ip_value = 'bam::ip*'
    current_time = datetime.now(timezone.utc)
    self_hosted_instances = []
    bam_ip_instances = []
 
    # Self-hosted runners
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
            if running_time.total_seconds() > 900 and not notified_tag:
                self_hosted_instances.append({
                    'Instance ID': instance_id,
                    'Launch Time': launch_time,
                    'Running Time': running_time
                })
                mark_instance_as_notified(instance_id)
 
    # Bamboo IP pattern
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
 
    # Combine into one SNS message
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
        send_sns_notification("Self-Hosted Runners (2+ hrs) & Bamboo Elastic Instances (3+ hrs) Running", final_message)
    else:
        print("No instances exceeding thresholds.")
 
    # ===== Extended Logic Begins Here =====
 
    # Extract all self-hosted instance IDs you just listed in the mail
    mail_runner_ids = [inst['Instance ID'] for inst in self_hosted_instances]
 
    # Retrieve idle GitHub self-hosted runner names via your curl + jq pipeline
    try:
        cmd = [
            "bash", "-lc",
            '''curl -s -L \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/orgs/vitechsystems/actions/runners?per_page=100" \
| jq -r '
  .runners[]
  | select(
      .status == "online" and .busy == false
    )
  | .name
'''            ]
        result = subprocess.check_output(cmd, text=True)
        github_runner_ids = [line.strip() for line in result.splitlines() if line.strip()]
    except subprocess.CalledProcessError as e:
        print("Error fetching GitHub runner IDs:", e)
        github_runner_ids = []
 
    # Cross-check and stop matching instances
    overlap_ids = [iid for iid in mail_runner_ids if iid in github_runner_ids]
    if overlap_ids:
        for iid in overlap_ids:
            try:
                ec2_client.stop_instances(InstanceIds=[iid])
                print(f"Stopping EC2 instance: {iid}")
            except Exception as e:
                print(f"Failed to stop instance {iid}: {e}")
    else:
        print("No matching idle GitHub runner instances to stop.")
 
# Run the function
check_running_ec2_instances()
