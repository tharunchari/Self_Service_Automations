#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Enterprise Spend Alert v4 - FIXED
=========================================
Job 1 script: DETECT and ALERT only.
Does NOT send movement emails or log CSV.
Those happen in Job 2 AFTER actual Azure group move.

Usage:
  python3 claude_spend_alert_v4_complete.py \
    --threshold 100 \
    --dry-run false \
    --only-executives false \
    --executives "exec1@email.com,exec2@email.com"
"""

import os
import sys
import json
import argparse
import requests
import boto3
import csv
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import logging


# ============================================================================
# LOGGING
# ============================================================================
def setup_logging():
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    return logging.getLogger(__name__)

logger = setup_logging()


# ============================================================================
# BUDGET TIER MANAGEMENT
# ============================================================================
class BudgetTierManager:
    BUDGET_TIERS = [
        {"limit": 20, "group_name": "Majesco-Claude-OIDC-20", "group_id": "2e4004c6-3dbb-4ffd-b52a-1ea7a8ed0a4c"},
        {"limit": 50, "group_name": "Majesco-Claude-OIDC-50", "group_id": "480c4f40-5b84-4375-bdf7-5d377f1aab4d"},
        {"limit": 100, "group_name": "Majesco-Claude-OIDC-100", "group_id": "ce177ee4-6f2b-469f-93c6-a2b0313fc43d"},
        {"limit": 200, "group_name": "Majesco-Claude-OIDC-200", "group_id": "71a1b6a7-f582-4b61-9893-d2fb4bfb3a74"},
        {"limit": 300, "group_name": "Majesco-Claude-OIDC-300", "group_id": "4bb117fa-eed6-4ca8-b46e-6ae4ecfa2154"},
        {"limit": 500, "group_name": "Majesco-Claude-OIDC-500", "group_id": "3ea9c6d1-8745-42b8-8584-84862bee113a"},
        {"limit": 1000, "group_name": "Majesco-Claude-OIDC-1K", "group_id": "76b75b08-3458-4c70-be41-bf1d190bd172"},
        {"limit": 1500, "group_name": "Majesco-Claude-OIDC-1.5K", "group_id": "6d2dc11e-7336-4f10-a966-ec93aa5b71aa"},
        {"limit": 2000, "group_name": "Majesco-Claude-OIDC-2K", "group_id": "c6feaca0-f818-4f4b-b1cc-21f6185ec8e8"},
    ]
    
    @classmethod
    def get_next_tier(cls, current_limit: float) -> Optional[Dict]:
        for tier in cls.BUDGET_TIERS:
            if tier['limit'] > current_limit:
                return tier.copy()
        return cls.BUDGET_TIERS[-1].copy()
    
    @classmethod
    def get_tier_by_limit(cls, limit: float) -> Optional[Dict]:
        for tier in cls.BUDGET_TIERS:
            if tier['limit'] == limit:
                return tier.copy()
        closest = min(cls.BUDGET_TIERS, key=lambda x: abs(x['limit'] - limit))
        return closest.copy()


# ============================================================================
# UPGRADE TRACKER - loads existing records to preserve initial_group
# ============================================================================
class UpgradeTracker:
    LOG_FILE = "claude_group_upgrades.csv"
    
    @classmethod
    def load_existing_upgrades(cls) -> Dict[str, Dict]:
        upgrades = {}
        if os.path.exists(cls.LOG_FILE):
            try:
                with open(cls.LOG_FILE, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        email = row.get('email', '').lower()
                        if email and email not in upgrades:
                            upgrades[email] = row
                logger.info(f"✓ Loaded {len(upgrades)} previous upgrade records")
            except Exception as e:
                logger.warning(f"Could not load previous upgrades: {e}")
        return upgrades


# ============================================================================
# EMAIL - only initial alert (movement emails sent by Job 2)
# ============================================================================
class EmailManager:
    def __init__(self, aws_region='us-east-1', dry_run=False):
        self.aws_region = aws_region
        self.dry_run = dry_run
        self.email_from = os.environ.get('EMAIL_FROM', 'no-reply@majesco.com')
        self.email_to_team = os.environ.get('EMAIL_TO_TEAM', 'ITGSV3locityDevOps@majesco.com')
        if not dry_run:
            self.ses_client = boto3.client('ses', region_name=aws_region)
    
    def send_email(self, to_email, subject, html_body):
        if self.dry_run:
            logger.info(f"[DRY RUN] Would send email to {to_email}: {subject}")
            return True
        try:
            self.ses_client.send_email(
                Source=self.email_from,
                Destination={'ToAddresses': [to_email]},
                Message={
                    'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                    'Body': {'Html': {'Data': html_body, 'Charset': 'UTF-8'}}
                }
            )
            logger.info(f"✓ Email sent to {to_email}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to send email to {to_email}: {e}")
            return False
    
    def build_initial_alert_html(self, exhausted_users, approaching_users, threshold):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        exhausted_count = len(exhausted_users)
        approaching_count = len(approaching_users)
        
        html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Arial, sans-serif; background: #f5f5f5; color: #333; }}
    .container {{ max-width: 900px; margin: 0 auto; background: #fff; padding: 40px; border-radius: 8px; }}
    h2 {{ color: #0a3d62; margin: 0; font-size: 24px; padding: 0 0 10px 0; border-bottom: 3px solid #0073e6; }}
    .timestamp {{ color: #666; font-size: 13px; margin: 15px 0; }}
    .summary {{ background: #f0f8ff; border-left: 4px solid #0073e6; padding: 15px; margin: 20px 0; border-radius: 4px; font-size: 14px; }}
    .alert-red {{ background: #f8d7da; border-left: 4px solid #d32f2f; color: #721c24; padding: 15px; margin: 20px 0; border-radius: 4px; font-weight: bold; }}
    h3 {{ color: #0a3d62; font-size: 16px; margin: 25px 0 15px 0; border-bottom: 2px solid #0073e6; padding-bottom: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; }}
    th {{ background: #0073e6; color: white; padding: 12px; text-align: left; font-weight: bold; border: 1px solid #ddd; }}
    td {{ padding: 12px; border: 1px solid #ddd; }}
    tr:nth-child(even) {{ background: #f9f9f9; }}
    .status-exhausted {{ color: #d32f2f; font-weight: bold; }}
    .status-approaching {{ color: #ff6f00; font-weight: bold; }}
    .action-section {{ background: #e8f5e9; border-left: 4px solid #4caf50; padding: 15px; margin: 20px 0; border-radius: 4px; }}
    .footer {{ font-size: 12px; color: #888; margin-top: 30px; padding-top: 15px; border-top: 1px solid #ddd; line-height: 1.8; }}
</style></head>
<body><div class="container">
    <h2>Claude Enterprise - Daily Spend Limits Report</h2>
    <div class="timestamp">Generated: {timestamp}</div>
    
    <div class="summary">
        <b>Total Users Flagged:</b> {exhausted_count + approaching_count} &nbsp; | &nbsp;
        <b>Exhausted (100%+):</b> <span style="color: #d32f2f; font-weight: bold;">{exhausted_count}</span> &nbsp; | &nbsp;
        <b>Approaching ({threshold}%+):</b> {approaching_count} &nbsp; | &nbsp;
        <b>Threshold:</b> {threshold}%
    </div>"""
        
        if exhausted_count > 0:
            html += f"""
    <div class="alert-red">⚠️ WARNING: {exhausted_count} user(s) have exhausted their monthly spend limit and may be blocked. Immediate action recommended.</div>"""
        
        if exhausted_users:
            html += """
    <h3>🔴 Users at or Above 100% Spend (EXHAUSTED)</h3>
    <table><tr>
        <th>User Email</th><th>Monthly Limit</th><th>Amount Spent</th><th>Usage %</th>
    </tr>"""
            for user in exhausted_users:
                html += f"""<tr>
        <td>{user['email']}</td>
        <td>${user['limit']:,.2f}</td>
        <td>${user['spent']:,.2f}</td>
        <td class="status-exhausted">{user['percentage']:.1f}%</td>
    </tr>"""
            html += "</table>"
        
        if approaching_users:
            html += f"""
    <h3>🟡 Users Approaching {threshold}% Spend</h3>
    <table><tr>
        <th>User Email</th><th>Monthly Limit</th><th>Amount Spent</th><th>Usage %</th>
    </tr>"""
            for user in approaching_users:
                html += f"""<tr>
        <td>{user['email']}</td>
        <td>${user['limit']:,.2f}</td>
        <td>${user['spent']:,.2f}</td>
        <td class="status-approaching">{user['percentage']:.1f}%</td>
    </tr>"""
            html += "</table>"
        
        html += """
    <div class="action-section">
        <strong>📋 Next Steps:</strong>
        <ul>
            <li>Review exhausted users above</li>
            <li>Automated upgrade workflow will process upgrades (if configured)</li>
            <li>Audit trail will be maintained in GitHub for month-end revert</li>
            <li>Detailed reports saved locally and in CI/CD pipeline</li>
        </ul>
    </div>
    
    <div class="footer">
        <p><strong>Claude Enterprise - Automated Spend Management</strong><br>
        ITGS V3locity DevOps | Majesco Technologies<br>
        This is an automated report. Do not reply to this email.<br>
        Schedule: Weekdays at 9 AM EST</p>
    </div>
</div></body></html>"""
        return html


# ============================================================================
# MAIN ALERT SYSTEM
# ============================================================================
class ClaudeSpendAlertV4:
    
    def __init__(self, threshold=100, dry_run=False, only_executives=False, executives=""):
        self.threshold = threshold
        self.dry_run = dry_run
        self.only_executives = only_executives
        
        self.executive_set = set()
        if executives:
            self.executive_set = set(
                email.strip().lower() for email in executives.split(',') if email.strip()
            )
        
        self.admin_key = os.environ.get('ANTHROPIC_ADMIN_KEY')
        self.aws_region = os.environ.get('AWS_REGION', 'us-east-1')
        
        if not self.admin_key:
            logger.error("ERROR: ANTHROPIC_ADMIN_KEY not set")
            sys.exit(1)
        
        self.email_manager = EmailManager(aws_region=self.aws_region, dry_run=dry_run)
        self.existing_upgrades = UpgradeTracker.load_existing_upgrades()
    
    def cents_to_dollars(self, cents_str):
        try:
            return float(cents_str) / 100
        except (ValueError, TypeError):
            return 0.0
    
    def fetch_all_users(self) -> List[Dict]:
        """Fetch ALL users with proper pagination using next_page."""
        url = "https://api.anthropic.com/v1/organizations/spend_limits/effective"
        headers = {
            'x-api-key': self.admin_key,
            'content-type': 'application/json'
        }
        
        all_users = []
        page = 1
        
        logger.info("🔄 Fetching users from Claude API...")
        
        try:
            params = {'limit': 100}
            
            while True:
                logger.info(f"   Fetching page {page}...")
                response = requests.get(url, headers=headers, params=params, timeout=30)
                response.raise_for_status()
                
                data = response.json()
                users = data.get('data', [])
                
                if not users:
                    logger.info("   Reached end of pagination")
                    break
                
                all_users.extend(users)
                logger.info(f"   Page {page}: {len(users)} users (Total: {len(all_users)})")
                
                # Use 'next_page' - this is what Claude API returns
                next_page = data.get('next_page')
                if not next_page:
                    logger.info("   Reached last page")
                    break
                
                params['page'] = next_page
                page += 1
            
            logger.info(f"✓ Fetched {len(all_users)} total users from {page} page(s)")
            return all_users
        
        except Exception as e:
            logger.error(f"✗ Failed to fetch users: {e}")
            sys.exit(1)
    
    def filter_flagged_users(self, users) -> Tuple[List[Dict], List[Dict]]:
        """Filter users into exhausted (100%+) and approaching (threshold%+)."""
        exhausted = []
        approaching = []
        
        for user in users:
            actor = user.get('actor', {})
            email = actor.get('email_address', 'Unknown').lower()
            name = actor.get('name', 'Unknown')
            user_id = actor.get('user_id', 'Unknown')
            
            limit = self.cents_to_dollars(user.get('amount', '0'))
            spent = self.cents_to_dollars(user.get('period_to_date_spend', '0'))
            
            if limit == 0:
                continue
            
            percentage = (spent / limit) * 100
            
            user_data = {
                'name': name, 'email': email, 'user_id': user_id,
                'limit': limit, 'spent': spent, 'percentage': percentage,
                'period': user.get('period', 'monthly'),
                'source': user.get('source', {}).get('type', 'unknown'),
                'spend_id': user.get('spend_limit_id', 'N/A')
            }
            
            if percentage >= 100:
                exhausted.append(user_data)
            elif percentage >= self.threshold:
                approaching.append(user_data)
        
        exhausted.sort(key=lambda x: x['percentage'], reverse=True)
        approaching.sort(key=lambda x: x['percentage'], reverse=True)
        
        logger.info(f"✓ Found {len(exhausted)} exhausted users (100%+)")
        logger.info(f"✓ Found {len(approaching)} approaching users ({self.threshold}%+)")
        
        return exhausted, approaching
    
    def prepare_upgrade_data(self, exhausted_users) -> List[Dict]:
        """Prepare upgrade data, applying executive filter if enabled."""
        upgrades = []
        
        logger.info(f"\n📊 Preparing upgrade data ({len(exhausted_users)} exhausted users)...")
        
        if self.only_executives:
            logger.info(f"   Filter: EXECUTIVES ONLY ({len(self.executive_set)} configured)")
            users_to_process = [
                u for u in exhausted_users if u['email'] in self.executive_set
            ]
            logger.info(f"   After filter: {len(users_to_process)} users")
        else:
            logger.info("   Filter: ALL USERS")
            users_to_process = exhausted_users
        
        for user in users_to_process:
            email = user['email']
            current_limit = user['limit']
            current_tier = BudgetTierManager.get_tier_by_limit(current_limit)
            next_tier = BudgetTierManager.get_next_tier(current_limit)
            
            # Preserve initial group for month-end revert
            if email in self.existing_upgrades:
                rec = self.existing_upgrades[email]
                initial_group_id = rec.get('initial_group_id')
                initial_group_name = rec.get('initial_group_name')
                upgrade_count = int(rec.get('upgrade_count', 1)) + 1
            else:
                initial_group_id = current_tier.get('group_id', '')
                initial_group_name = current_tier.get('group_name', '')
                upgrade_count = 1
            
            upgrade = {
                'email': email,
                'name': user['name'],
                'is_executive': email in self.executive_set,
                'current_limit': current_limit,
                'current_group': current_tier.get('group_name'),
                'current_group_id': current_tier.get('group_id'),
                'new_limit': next_tier.get('limit'),
                'new_group': next_tier.get('group_name'),
                'new_group_id': next_tier.get('group_id'),
                'spent': user['spent'],
                'percentage': user['percentage'],
                'initial_group_id': initial_group_id,
                'initial_group_name': initial_group_name,
                'upgrade_count': upgrade_count,
                'upn': email.lower().strip()
            }
            
            upgrades.append(upgrade)
            logger.info(f"   ✓ {email}: {upgrade['current_group']} → {upgrade['new_group']}")
        
        logger.info(f"\n✓ Prepared {len(upgrades)} upgrades for processing")
        return upgrades
    
    def run(self):
        """
        Execute Job 1: DETECT & ALERT ONLY.
        
        This script does:
          ✓ Fetch all users from Claude API
          ✓ Identify exhausted users
          ✓ Send initial alert email to admin team
          ✓ Generate upgrade_instructions.json for Job 2
        
        This script does NOT:
          ✗ Move users between groups (Job 2 does this)
          ✗ Send movement emails (Job 2 does this AFTER moving)
          ✗ Log to CSV (Job 2 does this AFTER moving)
          ✗ Commit to GitHub (Job 2 does this AFTER moving)
        """
        logger.info("=" * 70)
        logger.info("CLAUDE ENTERPRISE SPEND ALERT - JOB 1: DETECT & ALERT")
        logger.info("=" * 70)
        logger.info(f"Threshold: {self.threshold}%")
        logger.info(f"Dry Run: {self.dry_run}")
        logger.info(f"Only Executives (for upgrades): {self.only_executives}")
        logger.info(f"Executives Configured: {len(self.executive_set)}")
        logger.info("=" * 70)
        
        # Step 1: Fetch all users
        users = self.fetch_all_users()
        if not users:
            logger.warning("No users found")
            return False
        
        # Step 2: Filter users
        exhausted_users, approaching_users = self.filter_flagged_users(users)
        
        # Step 3: Send initial alert email to admin team (ALL exhausted users)
        logger.info("\n📬 Step 3: Sending initial alert to admin team...")
        subject = f"[ALERT] Claude Enterprise: {len(exhausted_users)} Exhausted + {len(approaching_users)} Approaching"
        html_body = self.email_manager.build_initial_alert_html(
            exhausted_users, approaching_users, self.threshold
        )
        self.email_manager.send_email(self.email_manager.email_to_team, subject, html_body)
        
        # Step 4: Prepare upgrade data (with executive filtering)
        logger.info("\n📋 Step 4: Preparing upgrade data...")
        upgrades = self.prepare_upgrade_data(exhausted_users)
        
        if not upgrades:
            logger.info("No upgrades to process after filtering")
            logger.info("=" * 70)
            logger.info("Job 1 complete. No upgrades needed.")
            return True
        
        # Step 5: Generate upgrade_instructions.json for Job 2
        # NOTE: NO movement emails here. Job 2 sends emails AFTER actual move.
        logger.info("\n🔧 Step 5: Generating upgrade_instructions.json for Job 2...")
        output_data = {
            'timestamp': datetime.now().isoformat(),
            'total_users': len(upgrades),
            'send_mail_to_users': self.send_mail_to_users if hasattr(self, 'send_mail_to_users') else False,
            'upgrades': upgrades
        }
        with open('upgrade_instructions.json', 'w') as f:
            json.dump(output_data, f, indent=2)
        logger.info("✓ upgrade_instructions.json created")
        
        logger.info("\n" + "=" * 70)
        logger.info(f"✓ JOB 1 COMPLETE - {len(upgrades)} users ready for Job 2")
        logger.info("  Next: Job 2 will move users → send emails → commit to GitHub")
        logger.info("=" * 70)
        
        return True


# ============================================================================
# MAIN
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description='Claude Spend Alert v4 - Job 1: Detect & Alert')
    parser.add_argument('--threshold', type=int, default=100)
    parser.add_argument('--dry-run', type=lambda x: x.lower() in ('true', '1', 'yes'), default=False)
    parser.add_argument('--only-executives', type=lambda x: x.lower() in ('true', '1', 'yes'), default=False)
    parser.add_argument('--send-mail-to-users', type=lambda x: x.lower() in ('true', '1', 'yes'), default=False)
    parser.add_argument('--executives', type=str, default='')
    
    args = parser.parse_args()
    
    alerter = ClaudeSpendAlertV4(
        threshold=args.threshold,
        dry_run=args.dry_run,
        only_executives=args.only_executives,
        executives=args.executives
    )
    # Store for JSON output
    alerter.send_mail_to_users = args.send_mail_to_users
    
    success = alerter.run()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
