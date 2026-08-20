#!/usr/bin/env python3

import boto3
import botocore
import logging
import re
import time
from botocore.config import Config

# -------------------------------------------------------
# Configuration
# -------------------------------------------------------

REGIONS = [
    "us-east-1",
    "us-west-2"
]

SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:389180911583:VitechToolsNVAProd"

LOG_FILE = "association_cleanup.log"

DRY_RUN = False         # Set True to test without deleting
DELETE_DELAY = 0.2       # Seconds between deletes

INSTANCE_REGEX = re.compile(r"^i-[0-9a-f]{17}$")

# -------------------------------------------------------
# Logging
# -------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# -------------------------------------------------------
# Boto Retry Configuration
# -------------------------------------------------------

boto_config = Config(
    retries={
        "max_attempts": 10,
        "mode": "standard"
    }
)

sns = boto3.client("sns", region_name="us-east-1", config=boto_config)


def send_sns(subject, message):
    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
    except Exception as e:
        logging.error(f"Failed to send SNS notification: {e}")


NON_EXISTENT_STATES = {"terminated", "shutting-down"}


def instance_exists(ec2_client, instance_id):
    """
    Returns True if the instance exists and is not terminated/terminating.
    Returns False if the instance is not found, malformed, terminated,
    or in the process of shutting down.
    """

    try:
        response = ec2_client.describe_instances(
            InstanceIds=[instance_id]
        )

        reservations = response.get("Reservations", [])

        if not reservations:
            return False

        state = reservations[0]["Instances"][0]["State"]["Name"]

        if state in NON_EXISTENT_STATES:
            return False

        return True

    except botocore.exceptions.ClientError as e:

        code = e.response["Error"]["Code"]

        if code in [
            "InvalidInstanceID.NotFound",
            "InvalidInstanceID.Malformed"
        ]:
            return False

        raise


def process_region(region):

    logging.info("=" * 60)
    logging.info(f"Scanning Region: {region}")
    logging.info("=" * 60)

    ssm = boto3.client("ssm", region_name=region, config=boto_config)
    ec2 = boto3.client("ec2", region_name=region, config=boto_config)

    paginator = ssm.get_paginator("list_associations")

    scanned = 0
    deleted = 0
    skipped = 0
    failed = 0

    deleted_list = []
    debug_samples_logged = 0
    MAX_DEBUG_SAMPLES = 15

    for page in paginator.paginate():

        for association in page["Associations"]:

            scanned += 1

            association_id = association["AssociationId"]

            try:

                desc = ssm.describe_association(
                    AssociationId=association_id
                )

                assoc_desc = desc["AssociationDescription"]

                targets = assoc_desc.get("Targets", [])

                instance_targets = []

                for target in targets:

                    if target.get("Key", "").lower() == "instanceids":

                        for value in target.get("Values", []):

                            if INSTANCE_REGEX.match(value):
                                instance_targets.append(value)

                # Legacy associations (created via the old InstanceId
                # parameter instead of Targets) carry the instance ID
                # directly on the association, not inside Targets.
                legacy_instance_id = assoc_desc.get("InstanceId")

                if legacy_instance_id and INSTANCE_REGEX.match(legacy_instance_id):
                    instance_targets.append(legacy_instance_id)

                if not instance_targets:
                    skipped += 1
                    if debug_samples_logged < MAX_DEBUG_SAMPLES:
                        logging.info(
                            f"[SAMPLE] Skipped {association_id}: no matching "
                            f"instance target. Targets={targets} "
                            f"InstanceId={legacy_instance_id!r}"
                        )
                        debug_samples_logged += 1
                    continue

                instance_targets = list(set(instance_targets))

                delete = True

                for instance in instance_targets:

                    if instance_exists(ec2, instance):
                        delete = False
                        if debug_samples_logged < MAX_DEBUG_SAMPLES:
                            logging.info(
                                f"[SAMPLE] Association {association_id} kept: "
                                f"instance {instance} still active"
                            )
                            debug_samples_logged += 1
                        break

                if delete:

                    if DRY_RUN:
                        logging.info(
                            f"[DRY RUN] Would delete {association_id}"
                        )

                    else:
                        ssm.delete_association(
                            AssociationId=association_id
                        )

                        logging.info(
                            f"Deleted Association: {association_id}"
                        )

                    deleted += 1
                    deleted_list.append(association_id)

                    time.sleep(DELETE_DELAY)

                else:
                    skipped += 1

            except Exception as e:

                failed += 1

                logging.exception(
                    f"Failed processing association "
                    f"{association_id}: {e}"
                )

    return {
        "region": region,
        "scanned": scanned,
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed,
        "deleted_ids": deleted_list
    }


def main():

    overall_scanned = 0
    overall_deleted = 0
    overall_skipped = 0
    overall_failed = 0

    report = []

    for region in REGIONS:

        result = process_region(region)

        report.append(result)

        overall_scanned += result["scanned"]
        overall_deleted += result["deleted"]
        overall_skipped += result["skipped"]
        overall_failed += result["failed"]

    summary = []

    summary.append("SSM Association Cleanup Summary")
    summary.append("")
    summary.append(f"Regions: {', '.join(REGIONS)}")
    summary.append("")
    summary.append(f"Total Associations : {overall_scanned}")
    summary.append(f"Deleted            : {overall_deleted}")
    summary.append(f"Skipped            : {overall_skipped}")
    summary.append(f"Failed             : {overall_failed}")
    summary.append("")

    for r in report:

        summary.append(
            f"{r['region']} -> "
            f"Scanned={r['scanned']}, "
            f"Deleted={r['deleted']}, "
            f"Skipped={r['skipped']}, "
            f"Failed={r['failed']}"
        )

    summary_text = "\n".join(summary)

    logging.info("\n" + summary_text)

    send_sns(
        "AWS SSM Association Cleanup Report",
        summary_text
    )


if __name__ == "__main__":
    main()
