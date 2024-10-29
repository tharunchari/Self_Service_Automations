import boto3
from datetime import datetime, timezone
 
def send_sns_notification(subject, message):
    # Create an SNS client
    sns_client = boto3.client('sns')
    # Define the ARN of your SNS topic
    topic_arn = 'arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd'
    try:
        # Publish the message to the SNS topic
        sns_client.publish(
            TopicArn=topic_arn,
            Message=message,
            Subject=subject
        )
        print("SNS notification sent successfully")
    except Exception as e:
        print("Failed to send SNS notification:", str(e))
 
def mark_instance_as_notified(instance_id):
    # Add a 'Notified' tag to the instance after sending the notification
    ec2_client = boto3.client('ec2')
    try:
        ec2_client.create_tags(Resources=[instance_id], Tags=[{'Key': 'Notified', 'Value': 'True'}])
        print(f"Instance {instance_id} marked as notified")
    except Exception as e:
        print(f"Failed to tag instance {instance_id}: {str(e)}")
 
def check_running_ec2_instances():
    # Create a Boto3 EC2 client
    ec2_client = boto3.client('ec2')
    # Define the tag keys and values to filter instances for each runner type
    tag_key = 'Name'
    self_hosted_runner_value = 'Github_Self_Hosted_Runner'
    bam_ip_value = 'bam::ip*'  # Pattern to match instance names starting with 'bam::ip'
    # Get the current time in UTC
    current_time = datetime.now(timezone.utc)
    # Lists to accumulate instances running for each type
    self_hosted_instances = []
    bam_ip_instances = []
 
    # Retrieve instances for self-hosted runners running for more than 2 hours
    response_self_hosted = ec2_client.describe_instances(Filters=[
        {'Name': f'tag:{tag_key}', 'Values': [self_hosted_runner_value]},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ])
    # Extract self-hosted runners running more than 2 hours and not previously notified
    for reservation in response_self_hosted['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            notified_tag = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Notified'), None)
            running_time = current_time - launch_time
            if running_time.total_seconds() > 7200:  # 2 hours = 7200 seconds
                self_hosted_instances.append({
                    'Instance ID': instance_id,
                    'Launch Time': launch_time,
                    'Running Time': running_time
                })
                mark_instance_as_notified(instance_id)
 
    # Retrieve instances for bam::ip* pattern running for more than 3 hours
    response_bam_ip = ec2_client.describe_instances(Filters=[
        {'Name': f'tag:{tag_key}', 'Values': [bam_ip_value]},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ])
    # Extract bam::ip* instances running more than 3 hours and not previously notified
    for reservation in response_bam_ip['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            notified_tag = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Notified'), None)
            running_time = current_time - launch_time
            if running_time.total_seconds() > 10800:  # 3 hours = 10800 seconds
                bam_ip_instances.append({
                    'Instance ID': instance_id,
                    'Launch Time': launch_time,
                    'Running Time': running_time
                })
                mark_instance_as_notified(instance_id)
 
    # Self-Hosted Runners
    if self_hosted_instances:
        subject_self_hosted = "Self-Hosted Github Runners Running for More Than 2 Hours"
        message_self_hosted = "The following self-hosted Github runners have been running for more than 2 hours:\n"
        for instance in self_hosted_instances:
            message_self_hosted += f"Instance ID: {instance['Instance ID']}\n"
            message_self_hosted += f"Launch Time: {instance['Launch Time']}\n"
            message_self_hosted += f"Running Time: {instance['Running Time']}\n\n"
        send_sns_notification(subject_self_hosted, message_self_hosted)
    else:
        print("No self-hosted runners running for more than 2 hours.")
 
    # bam::ip* Instances
    if bam_ip_instances:
        subject_bam_ip = "bam::ip* Instances Running for More Than 3 Hours"
        message_bam_ip = "The following bam::ip* instances have been running for more than 3 hours:\n"
        for instance in bam_ip_instances:
            message_bam_ip += f"Instance ID: {instance['Instance ID']}\n"
            message_bam_ip += f"Launch Time: {instance['Launch Time']}\n"
            message_bam_ip += f"Running Time: {instance['Running Time']}\n\n"
        send_sns_notification(subject_bam_ip, message_bam_ip)
    else:
        print("No bam::ip* instances running for more than 3 hours.")
 
# Run the function to check running EC2 instances and send SNS notifications
check_running_ec2_instances()
