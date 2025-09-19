import boto3
import requests
import os
from datetime import datetime, timezone

# ==============================
# Configurable variables
# ==============================
AWS_REGION = "us-east-1"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd"
GITHUB_ORG = "vitechsystems"
GITHUB_TOKEN = os.environ.get("CLASSIC_PAT")  # Classic PAT from workflow env
DRY_RUN = True  # Change to False to actually terminate instances
THRESHOLD_MINUTES = 5
TAG_KEY = "Name"
TAG_VALUE = "Github_Self_Hosted_Runner"
# ==============================


def send_sns_notification(subject, message):
    sns_client = boto3.client("sns", region_name=AWS_REGION)
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject=subject
        )
        print("SNS notification sent successfully")
    except Exception as e:
        print("Failed to send SNS notification:", str(e))


def get_aws_instances(threshold_minutes=THRESHOLD_MINUTES, tag_key=TAG_KEY, tag_value=TAG_VALUE):
    ec2_client = boto3.client("ec2", region_name=AWS_REGION)
    current_time = datetime.now(timezone.utc)
    instances = []
    response = ec2_client.describe_instances(Filters=[
        {"Name": f"tag:{tag_key}", "Values": [tag_value]},
        {"Name": "instance-state-name", "Values": ["running"]}
    ])
    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            instance_id = instance["InstanceId"]
            launch_time = instance["LaunchTime"]
            running_time = current_time - launch_time
            if running_time.total_seconds() > threshold_minutes * 60:
                instances.append({
                    "InstanceId": instance_id,
                    "LaunchTime": launch_time,
                    "RunningTime": running_time
                })
    return instances


def get_github_org_runners(org, token):
    url = f"https://api.github.com/orgs/{org}/actions/runners"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json"
    }
    runners = []
    page = 1
    while True:
        response = requests.get(url, headers=headers, params={"per_page": 100, "page": page})
        if response.status_code != 200:
            print(f"GitHub API error: {response.status_code}: {response.text}")
            break
        data = response.json()
        runners.extend(data.get("runners", []))
        if len(data.get("runners", [])) < 100:
            break
        page += 1
    return runners


def terminate_instances(instance_ids, dry_run=DRY_RUN):
    ec2_client = boto3.client("ec2", region_name=AWS_REGION)
    try:
        if dry_run:
            print(f"[DRY RUN] Would terminate instances: {instance_ids}")
        else:
            ec2_client.terminate_instances(InstanceIds=instance_ids)
            print(f"Terminated instances: {instance_ids}")
    except Exception as e:
        print(f"Failed to terminate instances {instance_ids}: {str(e)}")


def main():
    if not GITHUB_TOKEN:
        print("Error: CLASSIC_PAT environment variable not set!")
        return

    if DRY_RUN:
        print("⚠️ Running in DRY RUN mode. No instances will actually be terminated.")

    aws_instances = get_aws_instances()
    aws_instance_map = {inst["InstanceId"]: inst for inst in aws_instances}
    aws_instance_ids = list(aws_instance_map.keys())
    print(f"AWS instances running >{THRESHOLD_MINUTES} mins: {aws_instance_ids}")

    github_runners = get_github_org_runners(GITHUB_ORG, GITHUB_TOKEN)
    print(f"Total GitHub runners: {len(github_runners)}")

    # Match AWS instance IDs with runner names
    matched_runners = []
    matched_instance_ids = set()
    for runner in github_runners:
        runner_name = runner.get("name", "")
        for inst_id in aws_instance_ids:
            if runner_name.startswith(inst_id):
                matched_runners.append({
                    "instance_id": inst_id,
                    "runner_id": runner.get("id"),
                    "runner_name": runner_name,
                    "status": runner.get("status"),
                    "busy": runner.get("busy")
                })
                matched_instance_ids.add(inst_id)
                break

    # 1. Idle/Offline matched runners
    idle_offline_instances = []
    for runner in matched_runners:
        status = runner.get("status", "").lower()
        busy = runner.get("busy", False)

        # Offline OR Idle (online but not busy)
        if status == "offline" or (status == "online" and not busy):
            idle_offline_instances.append(runner["instance_id"])

    # 2. Orphaned AWS instances (not in GitHub runner list)
    orphan_instances = [inst_id for inst_id in aws_instance_ids if inst_id not in matched_instance_ids]

    # Combine both sets
    instances_to_terminate = list(set(idle_offline_instances + orphan_instances))

    print(f"Idle/Offline AWS runners to terminate: {idle_offline_instances}")
    print(f"Orphan AWS instances to terminate (not in GitHub): {orphan_instances}")
    print(f"Final list of instances to terminate: {instances_to_terminate}")

    if instances_to_terminate:
        terminate_instances(instances_to_terminate)

        if not DRY_RUN:
            subject = "Terminated Idle/Orphaned Github Runners"
            message = "Self-hosted GitHub runners running for more than 55 minutes have been terminated.\n\n"

            if idle_offline_instances:
                message += "Idle or Offline runners:\n"
                for inst_id in idle_offline_instances:
                    inst = aws_instance_map.get(inst_id)
                    if inst:
                        message += f"Instance ID: {inst_id}\n"
                        message += f"Launch Time: {inst['LaunchTime']}\n"
                        message += f"Running Time: {inst['RunningTime']}\n\n"

            if orphan_instances:
                message += "Orphaned runner / failed registration:\n"
                for inst_id in orphan_instances:
                    inst = aws_instance_map.get(inst_id)
                    if inst:
                        message += f"Instance ID: {inst_id}\n"
                        message += f"Launch Time: {inst['LaunchTime']}\n"
                        message += f"Running Time: {inst['RunningTime']}\n\n"

            message += "Thanks,\nv3atlassianops"

            send_sns_notification(subject, message)
    else:
        print("No idle, offline, or orphaned instances found for termination.")



if __name__ == "__main__":
    main()
