import boto3
from datetime import datetime, timezone
 
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
        ec2_client.create_tags(Resources=[instance_id], Tags=[{'Key': 'Notified', 'Value': 'True'}])
        print(f"Instance {instance_id} marked as notified")
    except Exception as e:
        print(f"Failed to tag instance {instance_id}: {str(e)}")
 
def check_running_ec2_instances():
    ec2_client = boto3.client('ec2')
    current_time = datetime.now(timezone.utc)
 
    tag_key = 'Name'
    self_hosted_runner_value = 'Github_Self_Hosted_Runner'
    bam_ip_value = 'bam::ip*'
    tools_instance_value = 'Internal_Tools_Automation_Server'
 
    self_hosted_instances = []
    bam_ip_instances = []
    internal_tools_instances = []
 
    # Check Github Self-Hosted Runners (2 hours)
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
 
    # Check BAM Elastic Instances (3 hours)
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
 
    # Check Internal Tools Automation Server (1 hour)
    response_tools = ec2_client.describe_instances(Filters=[
        {'Name': f'tag:{tag_key}', 'Values': [tools_instance_value]},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ])
    for reservation in response_tools['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            running_time = current_time - launch_time
            notified_tag = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Notified'), None)
            if running_time.total_seconds() > 3600 and not notified_tag:
                internal_tools_instances.append({
                    'Instance ID': instance_id,
                    'Launch Time': launch_time,
                    'Running Time': running_time
                })
                mark_instance_as_notified(instance_id)
 
    # Send notifications
    if self_hosted_instances:
        subject = "Self-Hosted Github Runners Running for More Than 2 Hours"
        message = "The following self-hosted Github runners have been running for more than 2 hours:\n"
        for instance in self_hosted_instances:
            message += f"Instance ID: {instance['Instance ID']}\n"
            message += f"Launch Time: {instance['Launch Time']}\n"
            message += f"Running Time: {instance['Running Time']}\n\n"
        send_sns_notification(subject, message)
    else:
        print("No self-hosted runners running for more than 2 hours.")
 
    if bam_ip_instances:
        subject = "Bamboo Elastic Instances Running for More Than 3 Hours"
        message = "The following Bamboo Elastic Instances have been running for more than 3 hours:\n"
        for instance in bam_ip_instances:
            message += f"Instance ID: {instance['Instance ID']}\n"
            message += f"Launch Time: {instance['Launch Time']}\n"
            message += f"Running Time: {instance['Running Time']}\n\n"
        send_sns_notification(subject, message)
    else:
        print("No bam::ip* instances running for more than 3 hours.")
 
    if internal_tools_instances:
        subject = "Internal Tools Automation Server Running for More Than 1 Hour"
        message = "The following Internal_Tools_Automation_Server instance has been running for more than 1 hour:\n"
        for instance in internal_tools_instances:
            message += f"Instance ID: {instance['Instance ID']}\n"
            message += f"Launch Time: {instance['Launch Time']}\n"
            message += f"Running Time: {instance['Running Time']}\n\n"
        send_sns_notification(subject, message)
    else:
        print("No Internal_Tools_Automation_Server instance running for more than 1 hour.")
 
# Run it
check_running_ec2_instances()
