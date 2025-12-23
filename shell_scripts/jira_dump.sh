#!/bin/bash

# ==============================
# Configuration
# ==============================
BASE_DIR="/u01/dumps"
HOSTNAME="$(hostname)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DUMP_DIR="${BASE_DIR}/${TIMESTAMP}"
MAIL_TO="v3atlassianops@vitechinc.com"
MAIL_FROM="no-reply@vitechinc.com"
MAIL_SUBJECT="Jira Thread & CPU Dumps - ${HOSTNAME} - ${TIMESTAMP}"
ITERATIONS=6
SLEEP_INTERVAL=10

# ==============================
# Logging function
# ==============================
log() {
  echo "[`date '+%Y-%m-%d %H:%M:%S'`] $1"
}

# ==============================
# Find Jira PID
# ==============================
log "Detecting Jira PID..."
JIRA_PID=$(ps aux | grep -i jira | grep -i java | grep -v grep | awk '{print $2}')

if [[ -z "$JIRA_PID" ]]; then
  log "ERROR: Jira process not found. Exiting."
  echo "Jira process not running on ${HOSTNAME}" | \
    mailx -s "Jira Dump FAILED - ${HOSTNAME}" "$MAIL_TO"
  exit 1
fi

log "Jira PID detected: $JIRA_PID"

# ==============================
# Create dump directory
# ==============================
log "Creating dump directory: $DUMP_DIR"
mkdir -p "$DUMP_DIR"
if [[ $? -ne 0 ]]; then
  log "ERROR: Failed to create dump directory."
  exit 1
fi

cd "$DUMP_DIR" || exit 1

# ==============================
# Generate dumps
# ==============================
log "Starting dump collection..."

for i in $(seq 1 $ITERATIONS); do
  NOW_TS=$(date +%s)

  log "Iteration $i: Capturing CPU usage"
  top -b -H -p "$JIRA_PID" -n 1 > "jira_cpu_usage.${HOSTNAME}.${NOW_TS}.txt"
  if [[ $? -ne 0 ]]; then
    log "WARNING: Failed to capture CPU usage"
  fi

  log "Iteration $i: Capturing thread dump"
  jstack -l "$JIRA_PID" > "jira_threads.${HOSTNAME}.${NOW_TS}.txt"
  if [[ $? -ne 0 ]]; then
    log "WARNING: Failed to capture thread dump"
  fi

  sleep "$SLEEP_INTERVAL"
done

log "Dump collection completed"
pwd

# ==============================
# Send email with all files in folder
# ==============================
if ls "$DUMP_DIR"/* >/dev/null 2>&1; then
  log "Sending email with dump attachments"
  attachments=$(printf -- "-a %s " "$DUMP_DIR"/*)
  echo "Please find attached Jira CPU & thread dumps from ${HOSTNAME} (${TIMESTAMP})." | \
    mailx -r "$MAIL_FROM" -s "$MAIL_SUBJECT" $attachments "$MAIL_TO"

  if [[ $? -eq 0 ]]; then
    log "Email sent successfully"
  else
    log "ERROR: Failed to send email"
  fi
else
  log "ERROR: No dump files found to send"
fi

exit 0
