import boto3
from datetime import datetime, timezone
 
def send_sns_notification(subject, message):
    sns_client = boto3.client('sns')
    topic_arn = 'arn:aws:sns:us-east-1:389180911583:Testing'
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
 
def check_self_hosted_runners():
    ec2_client = boto3.client('ec2')
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
 
    if self_hosted_instances:
        subject = "Self-Hosted Github Runners Running for More Than 2 Hours"
        message = ""
        for inst in self_hosted_instances:
            message += f"Instance ID: {inst['Instance ID']}\n"
            message += f"Launch Time: {inst['Launch Time']}\n"
            message += f"Running Time: {inst['Running Time']}\n\n"
        send_sns_notification(subject, message)
    else:
        print("No self-hosted runners running for more than 2 hours.")
 
if __name__ == "__main__":
    check_self_hosted_runners()
