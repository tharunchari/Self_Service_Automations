import boto3
from datetime import datetime, timezone, timedelta
 
# SNS topic ARN
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:389180911583:Testing'
 
# AWS clients
ec2_client = boto3.client('ec2')
sns_client = boto3.client('sns')
 
def get_github_self_hosted_runners():
    """Fetch EC2 instances tagged as Github_Self_Hosted_Runner and running > 2 hours."""
    instances_info = []
    two_hours = timedelta(hours=2)
 
    response = ec2_client.describe_instances(
        Filters=[
            {'Name': 'tag:Name', 'Values': ['Github_Self_Hosted_Runner']},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ]
    )
 
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            launch_time = instance['LaunchTime']
            running_time = datetime.now(timezone.utc) - launch_time
 
            if running_time > two_hours:
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
    instances = get_github_self_hosted_runners()
 
    if not instances:
        print("No self-hosted runners running more than 2 hours found.")
        return
 
    # Print detailed list
    print("=== EC2 Self-Hosted Runners ===")
    for inst in instances:
        print(f"Instance ID: {inst['id']}")
        print(f"Launch Time: {inst['launch_time']}")
        print(f"Running Time: {inst['running_time']}")
 
    # Print just IDs
    print("\nEC2 Self-Hosted Runners ids")
    ids_list = [inst['id'] for inst in instances]
    for iid in ids_list:
        print(iid)
 
    # Send SNS with only IDs
    send_sns_notification(
        "EC2 Self-Hosted Runners Running > 2 Hours",
        "\n".join(ids_list)
    )
 
if __name__ == "__main__":
    main()
