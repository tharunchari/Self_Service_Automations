import boto3

from datetime import datetime, timezone
 
# SNS topic ARN

SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'
 
ec2_client = boto3.client('ec2')

sns_client = boto3.client('sns')
 
# Threshold in seconds (7200 sec = 2 hours)

RUNNING_TIME_THRESHOLD = 500
 
def get_ec2_instances():

    """Fetch running EC2 instances tagged as GitHub self-hosted runners."""

    instances_info = []

    response = ec2_client.describe_instances(

        Filters=[

            {'Name': 'instance-state-name', 'Values': ['running']},

            {'Name': 'tag:Name', 'Values': ['Github_Self_Hosted_Runner']}  # Filter by tag

        ]

    )
 
    for reservation in response['Reservations']:

        for instance in reservation['Instances']:

            launch_time = instance['LaunchTime']

            running_time = datetime.now(timezone.utc) - launch_time

            running_seconds = running_time.total_seconds()
 
            if running_seconds > RUNNING_TIME_THRESHOLD:

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
 
    print("=== EC2 Self-Hosted Runners (> 2 hours) ===")

    for inst in instances:

        print(f"Instance ID: {inst['id']}")

        print(f"Launch Time: {inst['launch_time']}")

        print(f"Running Time: {inst['running_time']}")
 
    # Collect IDs for SNS message

    ids_list = [inst['id'] for inst in instances]

    if ids_list:

        send_sns_notification(

            "EC2 Self-Hosted Runners > 2 hours",

            "\n".join(ids_list)

        )

        print("\nSNS notification sent with these IDs:")

        for iid in ids_list:

            print(iid)

    else:

        print("\nNo instances found running longer than 2 hours.")
 
if __name__ == "__main__":

    main()

 
