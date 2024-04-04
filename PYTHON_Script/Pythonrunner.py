import boto3

import requests

import socket

from datetime import datetime, timezone

import smtplib

from email.mime.multipart import MIMEMultipart

from email.mime.text import MIMEText

def send_email(subject, message):

    sender = 'svallabhuni@vitechinc.com'

    recipient = ['svallabhuni@vitechinc.com']

    msg = MIMEMultipart()

    msg['Subject'] = subject

    msg['From'] = sender

    msg['To'] = ', '.join(recipient)

    # Create a MIMEText object and add it to the email

    body = MIMEText(message)

    msg.attach(body)
 
    try:

        smtp_obj = smtplib.SMTP('smtp-relay.vitech.com')

        smtp_obj.sendmail(sender, recipient, msg.as_string())

        smtp_obj.quit()

        print("Email sent successfully")

    except Exception as e:

        print("Failed to send email:", str(e))

def check_url(url):

    try: 

        response = requests.head(url)

        return response.status_code

    except requests.exceptions.RequestException:

        return None

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

        # Calculate the duration

        running_time = current_time - launch_time

        if running_time.total_seconds() > 360: # 2 hours = 7200 seconds

            instance_details = {

                'Instance ID': instance_id,

                'Launch Time': launch_time,

                'Running Time': running_time

            }

            instances_to_notify.append(instance_details)
 
# Prepare email subject and body

#subject = "EC2 Instances Running for More Than 2 Hours"
subject = "EC2 Github_Self_Hosted_Runner Instances Running for More Than 5min"

message = ""

for instance in instances_to_notify:

    message += f"Instance ID: {instance['Instance ID']}\n"

    message += f"Launch Time: {instance['Launch Time']}\n"

    message += f"Running Time: {instance['Running Time']}\n\n"
 
# Send email if there are instances to notify

if instances_to_notify:

    send_email(subject, message)

