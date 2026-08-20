#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Enterprise Daily Spend Limits Alert
==========================================
Fetches all users from Claude Enterprise API, identifies those who have
exceeded their spend limits (or approaching a configurable threshold),
and sends a formatted HTML email via AWS SNS.

Usage:
  python3 claude_spend_alert.py [--threshold 100] [--dry-run true/false]

Environment Variables Required:
  - ANTHROPIC_ADMIN_KEY: Claude Enterprise admin API key
  - SNS_TOPIC_ARN: AWS SNS topic ARN for email
  - AWS_REGION: AWS region (default: us-east-1)
"""

import os
import sys
import json
import argparse
import requests
import boto3
import time
from typing import List, Dict
from datetime import datetime


class ClaudeSpendAlerter:
    """Fetch spend limits and send alerts for exhausted users."""
    
    def __init__(self, threshold: int = 100, dry_run: bool = False):
        """
        Initialize the alerter.
        
        Args:
            threshold: Percentage threshold to flag (default 100 = 100%+)
            dry_run: If True, don't actually send email
        """
        self.threshold = threshold
        self.dry_run = dry_run
        self.admin_key = os.environ.get('ANTHROPIC_ADMIN_KEY')
        self.aws_region = os.environ.get('AWS_REGION', 'us-east-1')
        self.email_to = os.environ.get('EMAIL_TO', 'alerts@example.com')
        self.email_from = os.environ.get('EMAIL_FROM', 'noreply@majesco.com')
        
        # Validate
        if not self.admin_key:
            print("ERROR: ANTHROPIC_ADMIN_KEY not set")
            sys.exit(1)
        
        if not self.email_to and not dry_run:
            print("ERROR: EMAIL_TO not set")
            sys.exit(1)
    
    def log(self, message: str, level: str = "INFO"):
        """Log with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = {
            "INFO": "[INFO]",
            "SUCCESS": "[✓]",
            "ERROR": "[✗]",
            "WARNING": "[!]"
        }.get(level, "[*]")
        print(f"{prefix} {timestamp} - {message}")
    
    def cents_to_dollars(self, cents_str: str) -> float:
        """Convert cents string to dollars."""
        try:
            return float(cents_str) / 100
        except (ValueError, TypeError):
            return 0.0
    
    def fetch_all_users(self) -> List[Dict]:
        """Fetch ALL users with pagination from Claude API."""
        url = "https://api.anthropic.com/v1/organizations/spend_limits/effective"
        headers = {
            'x-api-key': self.admin_key,
            'content-type': 'application/json'
        }
        
        all_users = []
        page = 1
        
        self.log(f"Fetching spend limits from Claude API...")
        
        try:
            params = {'limit': 100}
            
            while True:
                self.log(f"Fetching page {page}...")
                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                
                data = response.json()
                users = data.get('data', [])
                
                if not users:
                    self.log(f"Reached end of pagination")
                    break
                
                all_users.extend(users)
                self.log(f"Page {page}: {len(users)} users (Total: {len(all_users)})", "INFO")
                
                # Check for next page
                if not data.get('next_page'):
                    self.log(f"Reached last page", "INFO")
                    break
                
                params['page'] = data['next_page']
                page += 1
            
            self.log(f"Fetched {len(all_users)} total users", "SUCCESS")
            return all_users
        
        except Exception as e:
            self.log(f"Failed to fetch users: {e}", "ERROR")
            sys.exit(1)
    
    def filter_flagged_users(self, users: List[Dict]) -> List[Dict]:
        """Filter users who meet or exceed the threshold."""
        flagged = []
        
        self.log(f"Filtering users with {self.threshold}%+ usage...")
        
        for user in users:
            actor = user.get('actor', {})
            name = actor.get('name', 'Unknown')
            email = actor.get('email_address', 'Unknown')
            user_id = actor.get('user_id', 'Unknown')
            
            limit = self.cents_to_dollars(user.get('amount', '0'))
            spent = self.cents_to_dollars(user.get('period_to_date_spend', '0'))
            
            if limit == 0:
                continue
            
            percentage = (spent / limit) * 100
            
            # Flag if meets or exceeds threshold
            if percentage >= self.threshold:
                flagged.append({
                    'name': name,
                    'email': email,
                    'user_id': user_id,
                    'limit': limit,
                    'spent': spent,
                    'percentage': percentage,
                    'period': user.get('period', 'monthly'),
                    'source': user.get('source', {}).get('type', 'unknown'),
                    'spend_id': user.get('spend_limit_id', 'N/A')
                })
        
        flagged.sort(key=lambda x: x['percentage'], reverse=True)
        self.log(f"Found {len(flagged)} users at {self.threshold}%+ usage", "SUCCESS")
        
        return flagged
    
    def build_status_table(self, users: List[Dict]) -> str:
        """Build clean HTML table matching professional email style."""
        if not users:
            return "<p>No users found with usage at or above threshold.</p>"
        
        # Start table with proper styling
        html = '<table border="0" cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse; margin:15px 0;">\n'
        
        # Table header row
        html += '<tr style="background:#0073e6; color:#fff;">\n'
        html += '  <th style="padding:12px; text-align:left; border:1px solid #ddd; font-weight:bold;">User Email</th>\n'
        html += '  <th style="padding:12px; text-align:right; border:1px solid #ddd; font-weight:bold;">Monthly Limit</th>\n'
        html += '  <th style="padding:12px; text-align:right; border:1px solid #ddd; font-weight:bold;">Amount Spent</th>\n'
        html += '  <th style="padding:12px; text-align:right; border:1px solid #ddd; font-weight:bold;">Usage %</th>\n'
        html += '</tr>\n'
        
        # Table data rows
        for i, user in enumerate(users):
            # Alternate row colors
            bg_color = "#f9f9f9" if i % 2 == 0 else "#ffffff"
            
            # Color code the status: red for 100%+, orange for 75-99%
            if user['percentage'] >= 100:
                status_color = "#d32f2f"
                status_bold = "font-weight:bold;"
            else:
                status_color = "#ff6f00"
                status_bold = "font-weight:bold;"
            
            html += f'<tr style="background:{bg_color};">\n'
            html += f'  <td style="padding:12px; text-align:left; border:1px solid #ddd;">{user["email"]}</td>\n'
            html += f'  <td style="padding:12px; text-align:right; border:1px solid #ddd;">${user["limit"]:,.2f}</td>\n'
            html += f'  <td style="padding:12px; text-align:right; border:1px solid #ddd;">${user["spent"]:,.2f}</td>\n'
            html += f'  <td style="padding:12px; text-align:right; border:1px solid #ddd; color:{status_color}; {status_bold}">{user["percentage"]:.1f}%</td>\n'
            html += '</tr>\n'
        
        html += '</table>'
        return html
    
    def build_html_body(self, users: List[Dict]) -> str:
        """Build complete HTML email body with professional styling."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        exhausted_count = sum(1 for u in users if u['percentage'] >= 100)
        approaching_count = len(users) - exhausted_count
        
        # CSS Styling
        style = """
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
                background: #f5f5f5;
                color: #333;
                line-height: 1.6;
                padding: 20px;
                margin: 0;
            }
            .container {
                max-width: 900px;
                margin: 0 auto;
                background: #ffffff;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                padding: 30px;
            }
            h2 {
                color: #0073e6;
                font-size: 24px;
                margin: 0 0 5px 0;
                padding: 0 0 10px 0;
                border-bottom: 3px solid #0073e6;
            }
            .timestamp {
                color: #666;
                font-size: 13px;
                margin-bottom: 15px;
            }
            h3 {
                color: #0a3d62;
                font-size: 16px;
                margin: 20px 0 10px 0;
                border-bottom: 2px solid #0073e6;
                padding-bottom: 8px;
            }
            .summary-line {
                background: #f0f8ff;
                border-left: 4px solid #0073e6;
                padding: 12px 15px;
                margin: 15px 0;
                font-size: 14px;
                border-radius: 4px;
            }
            .alert-red {
                background: #f8d7da;
                border-left: 4px solid #d32f2f;
                color: #721c24;
                padding: 12px 15px;
                margin: 15px 0;
                border-radius: 4px;
                font-weight: bold;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 15px 0;
                background: #fff;
            }
            th {
                background: #0073e6;
                color: white;
                padding: 12px;
                text-align: left;
                border: 1px solid #ddd;
                font-weight: bold;
                font-size: 13px;
            }
            td {
                padding: 12px;
                border: 1px solid #ddd;
                font-size: 13px;
            }
            tr:nth-child(even) {
                background: #f9f9f9;
            }
            tr:nth-child(odd) {
                background: #ffffff;
            }
            .footer {
                font-size: 12px;
                color: #888;
                margin-top: 30px;
                padding-top: 15px;
                border-top: 1px solid #ddd;
                line-height: 1.8;
            }
        </style>
        """
        
        # Build HTML header
        html = f'<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n{style}\n</head>\n<body>\n<div class="container">\n'
        
        # Header
        html += f'<h2>Claude Enterprise - Daily Spend Limits Report</h2>\n<div class="timestamp">Generated: {timestamp}</div>\n'
        
        # Summary line
        html += '<div class="summary-line">\n'
        html += f'<b>Total Users Flagged:</b> {len(users)} &nbsp; | &nbsp; '
        html += f'<b>Exhausted (100%+):</b> <span style="color: #d32f2f; font-weight: bold;">{exhausted_count}</span> &nbsp; | &nbsp; '
        html += f'<b>Approaching ({self.threshold}%+):</b> {approaching_count} &nbsp; | &nbsp; '
        html += f'<b>Threshold:</b> {self.threshold}%\n'
        html += '</div>\n'
        
        # Alert (if exhausted users exist)
        if exhausted_count > 0:
            html += f'<div class="alert-red">⚠️ WARNING: {exhausted_count} user(s) have exhausted their monthly spend limit and may be blocked. Immediate action recommended.</div>\n'
        
        # Table Section
        html += f'<h3>Users at or Above {self.threshold}% Spend Threshold</h3>\n'
        html += self.build_status_table(users)
        
        # Footer
        html += '<div class="footer">\n'
        html += 'This is an automated report from Claude Enterprise Admin API.<br>\n'
        html += 'Sent automatically via GitHub Actions using AWS SNS<br>\n'
        html += 'Schedule: Weekdays at 9 AM EST\n'
        html += '<br>\n'
        html += '<br>\n'
        html += 'Thanks,<br>\n'
        html += 'ITGS V3locity DevOps<br>\n'
        html += '</div>\n'
        
        # Close HTML
        html += '</div>\n</body>\n</html>'
        
        return html
    
    def send_email_ses(self, subject: str, html_body: str) -> bool:
        """Send HTML email via AWS SES for proper rendering."""
        if self.dry_run:
            self.log("DRY RUN: Would send SES email", "INFO")
            return True
        
        try:
            self.log("Sending HTML email via AWS SES...")
            
            ses_client = boto3.client('ses', region_name=self.aws_region)
            
            response = ses_client.send_email(
                Source=self.email_from,
                Destination={'ToAddresses': [self.email_to]},
                Message={
                    'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                    'Body': {
                        'Html': {'Data': html_body, 'Charset': 'UTF-8'}
                    }
                }
            )
            
            self.log(f"Email sent successfully via SES. Message ID: {response['MessageId']}", "SUCCESS")
            return True
        
        except Exception as e:
            self.log(f"Failed to send email via SES: {e}", "ERROR")
            return False
    
    def save_report(self, users: List[Dict], html_body: str):
        """Save report files (text and HTML)."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Save HTML report
        html_file = f"spend_limits_report_{timestamp}.html"
        with open(html_file, 'w') as f:
            f.write(html_body)
        self.log(f"HTML report saved: {html_file}", "SUCCESS")
        
        # Save CSV report
        csv_file = f"spend_limits_report_{timestamp}.csv"
        with open(csv_file, 'w') as f:
            f.write("Email,Monthly Limit,Amount Spent,Usage %\n")
            for user in users:
                f.write(f'"{user["email"]}",${user["limit"]:.2f},${user["spent"]:.2f},{user["percentage"]:.1f}%\n')
        self.log(f"CSV report saved: {csv_file}", "SUCCESS")
    
    def run(self):
        """Execute the full workflow."""
        self.log("Starting Claude Spend Limits Alert...", "INFO")
        
        # Fetch all users
        users = self.fetch_all_users()
        
        # Filter users
        flagged_users = self.filter_flagged_users(users)
        
        # Build HTML
        html_body = self.build_html_body(flagged_users)
        
        # Save reports
        self.save_report(flagged_users, html_body)
        
        # Send email
        if flagged_users:
            exhausted_count = sum(1 for u in flagged_users if u['percentage'] >= 100)
            if exhausted_count > 0:
                subject = f"[ACTION REQUIRED] Claude Enterprise: {exhausted_count} User(s) Exhausted Spend Limit"
            else:
                subject = f"[WARNING] Claude Enterprise: {len(flagged_users)} User(s) Approaching Limit"
            
            self.send_email_ses(subject, html_body)
        else:
            self.log(f"No users at or above {self.threshold}% threshold. No email sent.", "INFO")
        
        self.log("Report generation complete!", "SUCCESS")


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(
        description='Claude Enterprise Daily Spend Limits Alert'
    )
    parser.add_argument(
        '--threshold',
        type=int,
        default=100,
        help='Percentage threshold to flag (default: 100). Set to 90 to alert at 90%+ usage.'
    )
    parser.add_argument(
        '--dry-run',
        type=lambda x: x.lower() in ('true', '1', 'yes'),
        default=False,
        help='Dry run mode: print report but don\'t send email (default: false)'
    )
    
    args = parser.parse_args()
    
    alerter = ClaudeSpendAlerter(
        threshold=args.threshold,
        dry_run=args.dry_run
    )
    
    alerter.run()


if __name__ == '__main__':
    main()
