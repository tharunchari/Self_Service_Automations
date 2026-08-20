#!/usr/bin/env bash

set -euo pipefail

export LC_ALL=C

# Configuration
RATE_LIMIT_THRESHOLD=150          # Wait before audit-log if < 150 remaining
WINDOW_DAYS=90
OUTPUT_DIR="./dormant-reports"
STATE_FILE=".dormant_state.json"
AVAILABLE_TOKENS=()
CURRENT_TOKEN_INDEX=0

usage() {
  cat <<'EOF'
Usage: ./dormant_user_report_multi.sh [options]

Options:
  --all-orgs                 Process all orgs
  --org <name>              Process specific org
  --window-days <days>      Lookback window (default: 90)
  --output-dir <dir>        Output directory
  --fresh                   Ignore checkpoint and start fresh

Examples:
  ./dormant_user_report_multi.sh --all-orgs
  ./dormant_user_report_multi.sh --all-orgs --fresh
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

log_info() {
  echo "[INFO] $*" >&2
}

log_warn() {
  echo "[WARN] $*" >&2
}

log_error() {
  echo "[ERROR] $*" >&2
}

# Save checkpoint state
save_state() {
  local processed=("$@")
  
  local remaining_json="["
  for org in "${ALL_ORGS[@]}"; do
    local found=0
    for p in "${processed[@]}"; do
      [[ "$org" == "$p" ]] && { found=1; break; }
    done
    [[ $found -eq 0 ]] && remaining_json+="\"$org\","
  done
  remaining_json="${remaining_json%,}]"
  
  cat >"$STATE_FILE" <<EOF
{
  "version": 1,
  "window_days": $WINDOW_DAYS,
  "cutoff_date": "$CUTOFF_DATE",
  "last_updated": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "processed_orgs": [$(printf '"%s",' "${processed[@]}" | sed 's/,$//')],
  "remaining_orgs": $remaining_json,
  "stats": {
    "success": ${SUCCESS_COUNT:-0},
    "failed": ${FAILED_COUNT:-0}
  }
}
EOF
  
  log_info "Checkpoint saved"
}

# Load checkpoint state
load_state() {
  [[ -f "$STATE_FILE" ]] || return 1
  
  log_info "Loading checkpoint..."
  
  local state
  state=$(cat "$STATE_FILE")
  
  WINDOW_DAYS=$(echo "$state" | jq -r '.window_days')
  CUTOFF_DATE=$(echo "$state" | jq -r '.cutoff_date')
  
  mapfile -t REMAINING_ORGS < <(echo "$state" | jq -r '.remaining_orgs[]')
  mapfile -t PROCESSED_ORGS < <(echo "$state" | jq -r '.processed_orgs[]')
  
  SUCCESS_COUNT=$(echo "$state" | jq -r '.stats.success')
  FAILED_COUNT=$(echo "$state" | jq -r '.stats.failed')
  
  log_info "Resuming from checkpoint:"
  log_info "  - Processed: ${#PROCESSED_ORGS[@]} orgs (success: $SUCCESS_COUNT, failed: $FAILED_COUNT)"
  log_info "  - Remaining: ${#REMAINING_ORGS[@]} orgs"
  
  return 0
}

discover_tokens() {
  log_info "Discovering GitHub tokens..."
  
  local config_file="$HOME/.config/gh/hosts.yml"
  [[ -f "$config_file" ]] || die "GitHub config not found"
  
  mapfile -t AVAILABLE_TOKENS < <(
    grep 'oauth_token:' "$config_file" \
      | awk '{print $2}' \
      | sort -u
  )
  
  [[ ${#AVAILABLE_TOKENS[@]} -gt 0 ]] || die "No tokens found"
  log_info "Found ${#AVAILABLE_TOKENS[@]} token(s)"
}

switch_token() {
  export GH_TOKEN="$1"
  CURRENT_TOKEN_INDEX=$2
}

# Get rate limit status
get_rate_limit() {
  local response
  response=$(gh api rate_limit --jq '.resources.core | {limit, remaining, reset}' 2>/dev/null || echo "")
  [[ -n "$response" ]] && echo "$response" || return 1
}

# PROACTIVE rate limit waiting - check BEFORE expensive calls
wait_if_needed() {
  local endpoint="$1"
  local status
  
  status=$(get_rate_limit) || return 0
  
  local remaining reset
  remaining=$(echo "$status" | jq -r '.remaining')
  reset=$(echo "$status" | jq -r '.reset')
  
  # For audit-log, require at least 150 quota points
  if [[ "$endpoint" == *"audit-log"* ]]; then
    if [[ $remaining -lt $RATE_LIMIT_THRESHOLD ]]; then
      log_warn "============================================"
      log_warn "Rate limit check: $remaining / 5000"
      log_warn "Audit-log requires ~150 quota points"
      log_warn "Insufficient quota - waiting for reset..."
      log_warn "Reset time: $(date -d @"$reset" '+%Y-%m-%d %H:%M:%S')"
      log_warn "============================================"
      
      local wait_seconds=$((reset - $(date +%s) + 5))
      if [[ $wait_seconds -gt 0 ]]; then
        # Save state before waiting
        save_state "${PROCESSED_ORGS[@]}"
        
        log_warn "Waiting $((wait_seconds / 60)) minutes..."
        sleep "$wait_seconds"
        
        log_info "Rate limit reset - resuming..."
        sleep 2
      fi
    fi
  fi
}

api_call() {
  local endpoint="$1"
  local jq_filter="$2"
  local output_file="$3"
  
  # Wait if needed BEFORE making the call
  wait_if_needed "$endpoint"
  
  # Make the call
  if gh api --paginate "$endpoint" --jq "$jq_filter" >"$output_file" 2>&1; then
    return 0
  fi
  
  # Check if it's a rate limit error
  if grep -q "API rate limit exceeded" "$output_file"; then
    log_warn "Rate limit error - waiting full reset time..."
    
    # Get reset time from error or rate_limit endpoint
    local status
    status=$(get_rate_limit) || return 1
    local reset
    reset=$(echo "$status" | jq -r '.reset')
    
    local wait_seconds=$((reset - $(date +%s) + 5))
    if [[ $wait_seconds -gt 0 ]]; then
      save_state "${PROCESSED_ORGS[@]}"
      log_warn "Waiting $((wait_seconds / 60)) minutes..."
      sleep "$wait_seconds"
      
      # Retry after wait
      log_info "Retrying after rate limit reset..."
      if gh api --paginate "$endpoint" --jq "$jq_filter" >"$output_file" 2>&1; then
        return 0
      fi
    fi
  fi
  
  return 1
}

get_user_orgs() {
  local tmpfile
  tmpfile=$(mktemp)
  trap "rm -f '$tmpfile'" RETURN
  
  if api_call "user/orgs?per_page=100" '.[].login' "$tmpfile"; then
    tr -d '\r' <"$tmpfile" | grep -v '^$' | LC_ALL=C sort -u
  fi
}

compute_cutoff_date() {
  local days="$1"

  if date -u -v-"${days}"d +%Y-%m-%d >/dev/null 2>&1; then
    date -u -v-"${days}"d +%Y-%m-%d
  elif date -u -d "${days} days ago" +%Y-%m-%d >/dev/null 2>&1; then
    date -u -d "${days} days ago" +%Y-%m-%d
  else
    die "could not compute cutoff date"
  fi
}

iso_to_epoch() {
  local iso="$1"
  [[ -z "$iso" ]] && return 1

  if date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null; then
    return 0
  elif date -u -d "$iso" +%s 2>/dev/null; then
    return 0
  fi

  return 1
}

days_since_iso() {
  local iso="$1"
  local now_epoch="$2"
  local ts
  
  ts=$(iso_to_epoch "$iso") || return 1
  echo $(((now_epoch - ts) / 86400))
}

process_org() {
  local org="$1"
  local cutoff_date="$2"
  local cutoff_iso="${cutoff_date}T00:00:00Z"
  local now_epoch="$3"
  
  local tmpdir="$OUTPUT_DIR/tmp_${org}"
  mkdir -p "$tmpdir"
  
  log_info "Processing: $org"
  
  local roster="$tmpdir/roster.txt"
  local audit="$tmpdir/audit.txt"
  local active="$tmpdir/active.txt"
  local seats="$tmpdir/seats.tsv"
  local dormant="$OUTPUT_DIR/${org}-dormant.tsv"
  local rescued="$OUTPUT_DIR/${org}-rescued.tsv"
  local summary="$OUTPUT_DIR/${org}-summary.txt"
  
  # Get members
  log_info "  ? Fetching members..."
  if ! api_call "orgs/${org}/members?per_page=100" '.[].login' "$roster"; then
    log_error "  ? Failed to fetch members"
    rm -rf "$tmpdir"
    return 1
  fi
  
  if [[ ! -s "$roster" ]]; then
    log_error "  ? No members found"
    rm -rf "$tmpdir"
    return 1
  fi
  
  tr -d '\r' <"$roster" | grep -v '^$' | LC_ALL=C sort -u >"$tmpdir/roster.clean"
  mv "$tmpdir/roster.clean" "$roster"
  
  local member_count
  member_count=$(wc -l <"$roster")
  log_info "  ? Found $member_count members"
  
  # Get audit log
  log_info "  ? Fetching audit log..."
  if ! api_call "orgs/${org}/audit-log?include=all&per_page=100&phrase=created:>=${cutoff_date}" '.[] | .actor // empty' "$audit"; then
    log_error "  ? Audit log failed"
    rm -rf "$tmpdir"
    return 1
  fi
  
  tr -d '\r' <"$audit" | grep -v '^$' | LC_ALL=C sort -u >"$tmpdir/audit.clean"
  mv "$tmpdir/audit.clean" "$audit"
  
  LC_ALL=C comm -12 "$roster" "$audit" >"$active"
  
  local active_count
  active_count=$(wc -l <"$active" 2>/dev/null || echo "0")
  log_info "  ? Found $active_count active members"
  
  # Get Copilot seats
  log_info "  ? Fetching Copilot seats..."
  if api_call "orgs/${org}/copilot/billing/seats?per_page=100" '.seats[] | [.assignee.login, (.last_activity_at // ""), (.last_activity_editor // "")] | @tsv' "$seats" 2>/dev/null; then
    tr -d '\r' <"$seats" | LC_ALL=C sort -u >"$tmpdir/seats.clean"
    mv "$tmpdir/seats.clean" "$seats"
  else
    log_warn "  ? Copilot not available"
    : >"$seats"
  fi
  
  # Classify users
  : >"$dormant"
  : >"$rescued"
  
  local dormant_count=0
  local rescued_count=0
  
  while IFS= read -r user; do
    [[ -n "$user" ]] || continue
    
    if grep -Fxq "$user" "$active"; then
      continue
    fi
    
    if [[ -s "$seats" ]]; then
      local seat
      seat=$(awk -F'\t' -v u="$user" '$1 == u { print; exit }' "$seats") || true
      
      if [[ -n "$seat" ]]; then
        local login last editor
        IFS=$'\t' read -r login last editor <<<"$seat"
        
        local idle=""
        if [[ -n "$last" ]]; then
          idle=$(days_since_iso "$last" "$now_epoch" || echo "")
          
          if [[ -n "$idle" && $idle -lt $WINDOW_DAYS ]]; then
            rescued_count=$((rescued_count + 1))
            printf '%s\t%s\t%s\t%s\n' "$user" "${idle}d" "$last" "${editor:-unknown}" >>"$rescued"
            continue
          fi
        fi
        
        dormant_count=$((dormant_count + 1))
        [[ -n "$idle" ]] && idle="${idle}d" || idle="no activity"
        printf '%s\t%s\t%s\t%s\n' "$user" "$idle" "$last" "${editor:-unknown}" >>"$dormant"
        continue
      fi
    fi
    
    dormant_count=$((dormant_count + 1))
    printf '%s\t%s\n' "$user" "no copilot" >>"$dormant"
  done <"$roster"
  
  # Write summary
  cat >"$summary" <<EOF
Organization: $org
Window: ${WINDOW_DAYS} days
Cutoff: ${cutoff_date}
Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

=== SUMMARY ===
Total Members: $member_count
Audit Log Active: $active_count
Dormant: $dormant_count
Rescued: $rescued_count

=== DORMANT USERS ===
Count: $(wc -l <"$dormant" 2>/dev/null || echo "0")
$(cat "$dormant" 2>/dev/null || echo "  - none")

=== RESCUED USERS ===
Count: $(wc -l <"$rescued" 2>/dev/null || echo "0")
$(cat "$rescued" 2>/dev/null || echo "  - none")
EOF

  rm -rf "$tmpdir"
  log_info "  ? Done: $org"
  return 0
}

main() {
  require_cmd gh
  require_cmd jq
  require_cmd awk
  
  local fresh=0
  local all_orgs=0
  local orgs=()
  
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all-orgs) all_orgs=1; shift ;;
      --org) orgs+=("$2"); shift 2 ;;
      --window-days) WINDOW_DAYS="$2"; shift 2 ;;
      --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
      --fresh) fresh=1; shift ;;
      --help) usage; exit 0 ;;
      *) die "Unknown option: $1" ;;
    esac
  done
  
  mkdir -p "$OUTPUT_DIR"
  
  # Initialize variables
  local PROCESSED_ORGS=()
  local REMAINING_ORGS=()
  local SUCCESS_COUNT=0
  local FAILED_COUNT=0
  local ALL_ORGS=()
  local CUTOFF_DATE=""
  
  # Try to resume from checkpoint
  if [[ $fresh -eq 0 ]] && load_state; then
    orgs=("${REMAINING_ORGS[@]}")
  else
    # Fresh start
    discover_tokens
    switch_token "${AVAILABLE_TOKENS[0]}" 0
    CUTOFF_DATE=$(compute_cutoff_date "$WINDOW_DAYS")
    
    if [[ $all_orgs -eq 1 ]]; then
      log_info "Discovering orgs..."
      mapfile -t orgs < <(get_user_orgs | sort -u)
    fi
  fi
  
  [[ ${#orgs[@]} -gt 0 ]] || die "No orgs to process"
  
  ALL_ORGS=("${orgs[@]}")
  CUTOFF_DATE="${CUTOFF_DATE:-$(compute_cutoff_date $WINDOW_DAYS)}"
  
  log_info "=========================================="
  log_info "Organizations: ${#orgs[@]}"
  log_info "Window: $WINDOW_DAYS days"
  log_info "Cutoff: $CUTOFF_DATE"
  log_info "=========================================="
  echo
  
  local now_epoch
  now_epoch=$(date -u +%s)
  
  # Process each org
  for org in "${orgs[@]}"; do
    if process_org "$org" "$CUTOFF_DATE" "$now_epoch"; then
      PROCESSED_ORGS+=("$org")
      SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
      FAILED_COUNT=$((FAILED_COUNT + 1))
      # Save state after each failure
      save_state "${PROCESSED_ORGS[@]}"
    fi
  done
  
  # Summary
  echo
  log_info "=========================================="
  log_info "COMPLETE"
  log_info "=========================================="
  log_info "Success: $SUCCESS_COUNT"
  log_info "Failed: $FAILED_COUNT"
  log_info "Output: $OUTPUT_DIR"
  log_info "=========================================="
  
  # Clean up on complete success
  if [[ $FAILED_COUNT -eq 0 && -f "$STATE_FILE" ]]; then
    rm -f "$STATE_FILE"
  fi
}

main "$@"
