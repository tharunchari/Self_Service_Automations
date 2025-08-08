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
 
def check_bamboo_instances():
    ec2_client = boto3.client('ec2')
    tag_key = 'Name'
    bam_ip_value = 'bam::ip*'
    current_time = datetime.now(timezone.utc)
 
    notified_instances = set()  # To avoid duplicate emails in same run
    bam_ip_instances = []
 
    response = ec2_client.describe_instances(Filters=[
        {'Name': f'tag:{tag_key}', 'Values': [bam_ip_value]},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ])
 
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            running_time = current_time - launch_time
 
            if running_time.total_seconds() > 10800:  # > 3 hours
                if instance_id not in notified_instances:
                    notified_instances.add(instance_id)
                    bam_ip_instances.append({
                        'Instance ID': instance_id,
                        'Launch Time': launch_time,
                        'Running Time': running_time
                    })
 
    if bam_ip_instances:
        subject = "Bamboo Elastic Instances Running for More Than 3 Hours"
        message = ""
        for inst in bam_ip_instances:
            message += f"Instance ID: {inst['Instance ID']}\n"
            message += f"Launch Time: {inst['Launch Time']}\n"
            message += f"Running Time: {inst['Running Time']}\n\n"
        send_sns_notification(subject, message)
    else:
        print("No Bamboo Elastic Instances running for more than 3 hours.")
 
if __name__ == "__main__":
    check_bamboo_instances()
