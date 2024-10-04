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
    # Define the tag key and value to filter instances
    tag_key = 'Name'
    tag_value = 'Github_Self_Hosted_Runner'
    # Get the current time in UTC
    current_time = datetime.now(timezone.utc)
    # List to accumulate instances running for more than 2 hours
    instances_to_notify = []
    # Retrieve instance descriptions based on the tag filter
    response = ec2_client.describe_instances(Filters=[
        {
            'Name': f'tag:{tag_key}',
            'Values': [tag_value]
        },
        {
            'Name': 'instance-state-name',
            'Values': ['running']
        }
    ])
    # Extract instance details
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
                instances_to_notify.append(instance_details)
    # Prepare email subject and body
    subject = "EC2 Github_Self_Hosted_Runner Instances Running for More Than 2 hours"
    message = ""
    for instance in instances_to_notify:
        message += f"Instance ID: {instance['Instance ID']}\n"
        message += f"Launch Time: {instance['Launch Time']}\n"
        message += f"Running Time: {instance['Running Time']}\n\n"
    # Send SNS notification if there are instances to notify
    if instances_to_notify:
        send_sns_notification(subject, message)
 
# Run the function to check running EC2 instances and send notifications
check_running_ec2_instances()
