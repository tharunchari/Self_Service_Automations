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
 
def check_automation_server_runtime():
    ec2_client = boto3.client('ec2')
    tag_key = 'Name'
    tag_value = 'Internal_Tools_Automation_Server'
    current_time = datetime.now(timezone.utc)
    notify_threshold_seconds = 3600  # 1 hour
 
    response = ec2_client.describe_instances(Filters=[
        {'Name': f'tag:{tag_key}', 'Values': [tag_value]},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ])
 
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            notified_tag = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Notified'), None)
            running_time = current_time - launch_time
 
            if running_time.total_seconds() > notify_threshold_seconds and not notified_tag:
                subject = "Internal Tools Automation Server Running > 1 Hour"
                message = f"The EC2 instance 'Internal_Tools_Automation_Server' has been running for more than 1 hour:\n\n" \
                          f"Instance ID: {instance_id}\n" \
                          f"Launch Time: {launch_time}\n" \
                          f"Running Time: {running_time}\n"
                send_sns_notification(subject, message)
                mark_instance_as_notified(instance_id)
            else:
                print(f"No notification needed for instance {instance_id}.")
 
# Run it
check_automation_server_runtime()
