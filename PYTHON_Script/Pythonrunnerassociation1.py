import boto3
import subprocess 

# Define the ARN of your SNS topic

sns_topic_arn = 'arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd'
 


def send_sns_notification(subject, message):

    sns_client = boto3.client('sns', region_name='us-east-1')

    try:

        response = sns_client.publish(

            TopicArn=sns_topic_arn,

            Subject=subject,

            Message=message

        )

        print("SNS notification sent successfully. Message ID:", response['MessageId'])

    except Exception as e:

        print("Failed to send SNS notification:", str(e))
 
def main():

    try:

        # Run AWS CLI command to get the number of associations

        command = "aws ssm list-associations --query 'length(Associations[*])' --region us-east-1"

        output = subprocess.check_output(command, shell=True).decode().strip()

        associations_count = int(output)

        # Check if associations count is greater than 50

        if associations_count > 7:

            print(f"The number of associations is greater than 50: {associations_count}")

            # Optionally, you can also print the full output

            print("Full output:", output)

            # Send SNS notification

            subject = "AWS Jira Account SSM Association Alerts"

            message = f"The number of associations  {associations_count}. Action may be required."

            send_sns_notification(subject, message)

        else:

            print("The number of associations is not greater than 50:", associations_count)

    except subprocess.CalledProcessError as e:

        print("Failed to run AWS CLI command:", str(e))

    except ValueError:

        print("Invalid output from AWS CLI command. Unable to determine associations count.")
 

if __name__ == "__main__":

    main()

