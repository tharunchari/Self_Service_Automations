#!/usr/bin/env python3
"""
Fetch Claude.ai Enterprise spend limits using the Admin API.

Requires:
- CLAUDE_ADMIN_API_KEY environment variable set with Admin API key
- Key must have read:spend_limits scope
- Organization must be Claude.ai Enterprise with usage credits enabled

API Reference:
https://support.claude.com/en/articles/15330651-claude-enterprise-admin-api-reference-guide
"""

import os
import csv
import json
from datetime import datetime
from pathlib import Path
import requests
import sys

# Configuration
THRESHOLD_PERCENTAGE = 0.90  # 90% threshold
API_BASE_URL = "https://api.anthropic.com/v1"
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)

def get_admin_api_key():
    """Retrieve Admin API key from environment."""
    key = os.environ.get("CLAUDE_ADMIN_API_KEY")
    if not key:
        print("❌ CLAUDE_ADMIN_API_KEY environment variable not set")
        print("   Set this to your Claude.ai Enterprise Admin API key")
        sys.exit(1)
    return key

def fetch_spend_limits(api_key):
    """Fetch effective spend limits for all org members from Claude.ai Admin API."""
    endpoint = f"{API_BASE_URL}/organizations/spend_limits/effective"
    
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    try:
        response = requests.get(endpoint, headers=headers, timeout=10)
        
        if response.status_code == 401:
            print("❌ Authentication failed")
            print("   Verify your Admin API key is correct and has read:spend_limits scope")
            return None
        
        if response.status_code == 403:
            print("❌ Permission denied")
            print("   Your key may not have read:spend_limits scope")
            return None
        
        if response.status_code == 429:
            print("❌ Rate limit exceeded (60 requests/min)")
            return None
        
        if response.status_code != 200:
            print(f"❌ API error: {response.status_code}")
            print(f"   {response.text}")
            return None
        
        data = response.json()
        print(f"✅ Fetched spend limits for {len(data.get('data', []))} members")
        return data.get('data', [])
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Request error: {e}")
        return None

def parse_spend_data(spend_data):
    """Parse API response into user records with spend info."""
    users = []
    
    for record in spend_data:
        actor = record.get('actor', {})
        
        # Extract spend limit info
        amount = record.get('amount')
        if amount is None:
            limit = float('inf')  # Unlimited
        else:
            limit = float(amount) / 100  # Convert cents to dollars
        
        # Extract spend and metadata
        spend = float(record.get('period_to_date_spend', 0) or 0)
        currency = record.get('currency', 'USD')
        scope = record.get('source', {}).get('type', 'unknown')
        
        user = {
            'name': actor.get('name', 'Unknown'),
            'email': actor.get('email_address', 'N/A'),
            'user_id': actor.get('user_id', ''),
            'allocated_limit': limit,
            'period_to_date_spend': spend,
            'currency': currency,
            'limit_source': scope,
            'deleted': actor.get('deleted', False),
        }
        
        users.append(user)
    
    return users

def identify_budget_alerts(users, threshold=THRESHOLD_PERCENTAGE):
    """Filter users who are at or above the threshold."""
    alerts = []
    
    for user in users:
        # Skip deleted users
        if user['deleted']:
            continue
        
        limit = user['allocated_limit']
        spend = user['period_to_date_spend']
        
        # Skip users with unlimited budget
        if limit == float('inf'):
            continue
        
        if limit <= 0:
            continue
        
        # Calculate percentage
        percentage = (spend / limit) * 100
        
        if percentage >= (threshold * 100):
            alerts.append({
                'name': user['name'],
                'email': user['email'],
                'user_id': user['user_id'],
                'allocated_limit': limit,
                'period_to_date_spend': spend,
                'percentage_used': round(percentage, 2),
                'remaining_budget': round(limit - spend, 2),
                'currency': user['currency'],
                'limit_source': user['limit_source'],
                'status': 'CRITICAL' if percentage >= 100 else 'WARNING'
            })
    
    # Sort by percentage (highest first)
    alerts.sort(key=lambda x: x['percentage_used'], reverse=True)
    return alerts

def generate_report(alerts):
    """Generate CSV report from alert data."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_filename = REPORTS_DIR / f"claude_ai_budget_alert_{timestamp}.csv"
    
    if not alerts:
        print("✅ No users at or exceeding 90% of their budget limit")
        return None
    
    try:
        with open(report_filename, 'w', newline='', encoding='utf-8') as f:
            fieldnames = [
                'Name',
                'Email',
                'User ID',
                'Allocated Budget ($)',
                'Period-to-Date Spend ($)',
                'Percentage Used (%)',
                'Remaining Budget ($)',
                'Currency',
                'Limit Source',
                'Status'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for alert in alerts:
                writer.writerow({
                    'Name': alert['name'],
                    'Email': alert['email'],
                    'User ID': alert['user_id'],
                    'Allocated Budget ($)': f"${alert['allocated_limit']:.2f}",
                    'Period-to-Date Spend ($)': f"${alert['period_to_date_spend']:.2f}",
                    'Percentage Used (%)': f"{alert['percentage_used']}%",
                    'Remaining Budget ($)': f"${alert['remaining_budget']:.2f}",
                    'Currency': alert['currency'],
                    'Limit Source': alert['limit_source'],
                    'Status': alert['status']
                })
        
        print(f"✅ Report generated: {report_filename}")
        print(f"   Found {len(alerts)} users at or exceeding {THRESHOLD_PERCENTAGE*100}% budget")
        return report_filename
    
    except Exception as e:
        print(f"❌ Error writing report: {e}")
        return None

def print_summary(alerts):
    """Print summary to console."""
    if not alerts:
        return
    
    print(f"\n🚨 BUDGET ALERT SUMMARY")
    print(f"   Threshold: {THRESHOLD_PERCENTAGE*100}%")
    print(f"   Users at risk: {len(alerts)}\n")
    
    for alert in alerts[:10]:  # Show top 10
        status_emoji = "🔴" if alert['status'] == 'CRITICAL' else "🟠"
        print(f"{status_emoji} {alert['name']:<30} {alert['percentage_used']:>6.1f}%  (${alert['period_to_date_spend']:>8.2f} / ${alert['allocated_limit']:.2f})")
    
    if len(alerts) > 10:
        print(f"\n   ... and {len(alerts) - 10} more")

def main():
    """Main execution."""
    print("📊 Claude.ai Enterprise Budget Alert Generator\n")
    
    # Get API key
    api_key = get_admin_api_key()
    
    # Fetch spend data
    print("🔄 Fetching spend limits from Claude.ai...")
    spend_data = fetch_spend_limits(api_key)
    
    if spend_data is None:
        print("\n❌ Failed to fetch spend limits")
        sys.exit(1)
    
    if not spend_data:
        print("⚠️  No spend limit data returned")
        sys.exit(0)
    
    # Parse data
    print("📋 Parsing user spend data...")
    users = parse_spend_data(spend_data)
    print(f"   Processed {len(users)} users")
    
    # Identify alerts
    print(f"🔍 Identifying users at {THRESHOLD_PERCENTAGE*100}% of budget...")
    alerts = identify_budget_alerts(users)
    
    # Generate report
    generate_report(alerts)
    
    # Print summary
    print_summary(alerts)
    
    print("\n✅ Done")

if __name__ == "__main__":
    main()
