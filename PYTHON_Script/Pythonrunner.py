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
 
def check_running_ec2_instances():
    # Create a Boto3 EC2 client
    ec2_client = boto3.client('ec2')
    # Define the tag keys and values to filter instances
    tag_key_1 = 'Name'
    tag_value_1 = 'Github_Self_Hosted_Runner'
    tag_value_2 = 'bam::vm-bamboo::bamboo'
    tag_value_pattern = 'bam::VM*'  # Pattern to match instance names starting with 'bam::VM'
    # Get the current time in UTC
    current_time = datetime.now(timezone.utc)
    # List to accumulate instances running for more than 2 hours and 5 minutes
    instances_to_notify_2h = []
    instances_to_notify_5m = []
    # Retrieve instance descriptions based on the tag filters
    response = ec2_client.describe_instances(Filters=[
        {
            'Name': f'tag:{tag_key_1}',
            'Values': [tag_value_1, tag_value_2]  # Match either specific name
        },
        {
            'Name': 'instance-state-name',
            'Values': ['running']
        }
    ])
    # Extract instance details for instances running more than 2 hours
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            # Calculate the running time
            running_time = current_time - launch_time
            if running_time.total_seconds() > 7200:  # 2 hours = 7200 seconds
                instance_details = {
                    'Instance ID': instance_id,
                    'Launch Time': launch_time,
                    'Running Time': running_time
                }
                instances_to_notify_2h.append(instance_details)
    # Add a separate filter for instances with a Name that starts with 'bam::VM' and running more than 5 minutes
    response_bam_vm = ec2_client.describe_instances(Filters=[
        {
            'Name': f'tag:{tag_key_1}',
            'Values': [tag_value_pattern]  # Match instances with Name starting with 'bam::VM'
        },
        {
            'Name': 'instance-state-name',
            'Values': ['running']
        }
    ])
    # Extract details for 'bam::VM*' instances running more than 5 minutes
    for reservation in response_bam_vm['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            # Calculate running time
            running_time = current_time - launch_time
            if running_time.total_seconds() > 300:  # 5 minutes = 300 seconds
                instance_details = {
                    'Instance ID': instance_id,
                    'Launch Time': launch_time,
                    'Running Time': running_time
                }
                instances_to_notify_5m.append(instance_details)
 
    # Prepare email subject and body
    subject = "EC2 Instances Notification"
    message = ""
    if instances_to_notify_2h:
        message += "Instances running for more than 2 hours:\n"
        for instance in instances_to_notify_2h:
            message += f"Instance ID: {instance['Instance ID']}\n"
            message += f"Launch Time: {instance['Launch Time']}\n"
            message += f"Running Time: {instance['Running Time']}\n\n"
    if instances_to_notify_5m:
        message += "Instances with name 'bam::VM*' running for more than 5 minutes:\n"
        for instance in instances_to_notify_5m:
            message += f"Instance ID: {instance['Instance ID']}\n"
            message += f"Launch Time: {instance['Launch Time']}\n"
            message += f"Running Time: {instance['Running Time']}\n\n"
    # Print the message to console for debugging
    if message:
        print("Instance(s) notification:")
        print(message)
    # Send SNS notification if there are instances to notify
    if message:
        send_sns_notification(subject, message)
    else:
        print("No instances found running for more than 2 hours or 'bam::VM*' running for more than 5 minutes.")
 
# Run the function to check running EC2 instances and print output
check_running_ec2_instances()
