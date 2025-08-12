import boto3

from datetime import datetime, timezone
 
# SNS topic ARN

SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'
 
ec2_client = boto3.client('ec2')

sns_client = boto3.client('sns')
 
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
 
def send_sns_notification(subject, message):

    """Send SNS notification."""

    sns_client.publish(

        TopicArn=SNS_TOPIC_ARN,

        Message=message,

        Subject=subject

    )
 
def main():

    instances = get_ec2_instances()
 
    # Prepare message text

    message_lines = ["=== EC2 Self-Hosted Runners (> 2 hours) ==="]

    for inst in instances:

        message_lines.append(f"Instance ID: {inst['id']}")

        message_lines.append(f"Launch Time: {inst['launch_time']}")

        message_lines.append(f"Running Time: {inst['running_time']}\n")

    message_lines.append("\nSNS notification sent with these IDs:")

    for inst in instances:

        message_lines.append(inst['id'])
 
    message_text = "\n".join(message_lines)
 
    # Print locally

    print(message_text)
 
    # Send SNS

    if instances:

        send_sns_notification("EC2 Self-Hosted Runners (> 2 hours)", message_text)
 
if __name__ == "__main__":

    main()

 
