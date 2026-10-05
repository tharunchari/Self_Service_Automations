#!/usr/bin/env python3
"""
GitHub Enterprise - Fetch Budget IDs and Generate CSV Files

Automatically fetches all budget IDs from GitHub Enterprise and generates:
1. budget_ids_mapping.csv - Complete reference (ID, Entity Name, Amount, Scope, Type)
2. budget_updates_ready.csv - Ready for updates (Entity Name, Budget ID, New Amount)

Usage:
    python3 get_budget_ids_and_csv.py -t <token> -e <enterprise>

Example:
    python3 get_budget_ids_and_csv.py -t ghp_xxx -e vitech

Output Files:
    budget_ids_mapping.csv       - Complete mapping (reference)
    budget_updates_ready.csv     - Ready to update (edit amounts and run)
"""

import argparse
import sys
import csv
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Install with: pip install requests")
    sys.exit(1)


def fetch_all_budgets(token: str, enterprise: str):
    """Fetch all budgets with their IDs from GitHub"""
    
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/json"
    })
    
    url = f"https://api.github.com/enterprises/{enterprise}/settings/billing/budgets"
    
    print(f"Fetching budgets from GitHub Enterprise: {enterprise}")
    print(f"URL: {url}")
    print("=" * 100)
    
    all_budgets = []
    page = 1
    per_page = 100
    
    while True:
        params = {"per_page": per_page, "page": page}
        
        try:
            response = requests.get(url, headers=session.headers, params=params, timeout=10)
            
            if response.status_code != 200:
                print(f"✗ Failed: {response.status_code}")
                print(f"Response: {response.text[:200]}")
                return None
            
            data = response.json()
            budgets = data.get('budgets', [])
            
            if not budgets:
                break
            
            all_budgets.extend(budgets)
            print(f"Page {page}: Fetched {len(budgets)} budgets (total: {len(all_budgets)})")
            
            if not data.get('has_next_page', False):
                break
            
            page += 1
        
        except Exception as e:
            print(f"✗ Error fetching page {page}: {e}")
            return None
    
    return all_budgets


def save_mapping_csv(budgets, filename="budget_ids_mapping.csv"):
    """Save complete mapping CSV (reference file)"""
    
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'Budget ID',
                'Budget Entity Name',
                'Budget Amount',
                'Consumed Amount',
                'Scope',
                'Type'
            ])
            writer.writeheader()
            
            for budget in budgets:
                writer.writerow({
                    'Budget ID': budget.get('id', ''),
                    'Budget Entity Name': budget.get('budget_entity_name', ''),
                    'Budget Amount': budget.get('budget_amount', ''),
                    'Consumed Amount': budget.get('consumed_amount', ''),
                    'Scope': budget.get('budget_scope', ''),
                    'Type': budget.get('budget_type', '')
                })
        
        print(f"✓ Saved mapping to: {filename} ({len(budgets)} budgets)")
        return True
    except Exception as e:
        print(f"✗ Failed to save mapping CSV: {e}")
        return False


def save_update_csv(budgets, default_amount=80, filename="budget_updates_ready.csv"):
    """Save update-ready CSV (ready to use with update script)"""
    
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'Budget Entity Name',
                'Budget ID',
                'New Budget Amount'
            ])
            writer.writeheader()
            
            for budget in budgets:
                writer.writerow({
                    'Budget Entity Name': budget.get('budget_entity_name', ''),
                    'Budget ID': budget.get('id', ''),
                    'New Budget Amount': default_amount
                })
        
        print(f"✓ Saved update CSV to: {filename} ({len(budgets)} budgets)")
        print(f"  Default amount set to: ${default_amount}")
        print(f"  Edit the amounts as needed before running update")
        return True
    except Exception as e:
        print(f"✗ Failed to save update CSV: {e}")
        return False


def display_budgets(budgets, max_display=20):
    """Display budgets in a formatted table"""
    
    print(f"\nFirst {max_display} budgets:")
    print("=" * 130)
    print(f"{'ID':<40} | {'Entity Name':<40} | {'Amount':<10} | {'Scope':<15} | {'Type':<15}")
    print("-" * 130)
    
    for budget in budgets[:max_display]:
        budget_id = budget.get('id', 'Unknown')[:40]
        entity_name = budget.get('budget_entity_name', 'Unknown')[:40]
        amount = f"${budget.get('budget_amount', 'N/A')}"
        scope = budget.get('budget_scope', 'Unknown')[:15]
        btype = budget.get('budget_type', 'Unknown')[:15]
        
        print(f"{budget_id:<40} | {entity_name:<40} | {amount:<10} | {scope:<15} | {btype:<15}")
    
    if len(budgets) > max_display:
        print(f"... and {len(budgets) - max_display} more budgets")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Fetch GitHub Budget IDs and generate CSV files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script automatically generates two CSV files:

1. budget_ids_mapping.csv
   - Complete reference with all budget details
   - For your records

2. budget_updates_ready.csv
   - Ready to use with update_budgets_final.py
   - Edit amounts as needed
   - Then run: python3 update_budgets_final.py -i budget_updates_ready.csv -t TOKEN -e ENTERPRISE

Examples:
  python3 get_budget_ids_and_csv.py -t ghp_xxx -e vitech
  python3 get_budget_ids_and_csv.py -t ghp_xxx -e vitech -d 100
  python3 get_budget_ids_and_csv.py -t ghp_xxx -e vitech --mapping-only
  python3 get_budget_ids_and_csv.py -t ghp_xxx -e vitech --update-only
        """
    )
    
    parser.add_argument("-t", "--token", required=True, help="GitHub personal access token")
    parser.add_argument("-e", "--enterprise", required=True, help="Enterprise name")
    parser.add_argument("-d", "--default-amount", type=float, default=80, 
                       help="Default amount for update CSV (default: 80)")
    parser.add_argument("--mapping-only", action="store_true", 
                       help="Generate only mapping CSV (not update CSV)")
    parser.add_argument("--update-only", action="store_true", 
                       help="Generate only update CSV (not mapping CSV)")
    parser.add_argument("--mapping-file", default="budget_ids_mapping.csv",
                       help="Output filename for mapping CSV")
    parser.add_argument("--update-file", default="budget_updates_ready.csv",
                       help="Output filename for update CSV")
    
    args = parser.parse_args()
    
    # Validate token
    if not args.token or not args.token.startswith('ghp_'):
        print("WARNING: Token doesn't look like a valid GitHub token (should start with 'ghp_')")
    
    print("\n" + "=" * 100)
    print("GitHub Enterprise Budget ID Fetcher")
    print("=" * 100)
    
    # Fetch budgets
    budgets = fetch_all_budgets(args.token, args.enterprise)
    
    if not budgets:
        print("\n✗ Failed to fetch budgets")
        print("Check:")
        print("  1. Token is valid and not expired")
        print("  2. Enterprise name is correct")
        print("  3. You have permission to access billing settings")
        sys.exit(1)
    
    print(f"\n✓ Successfully fetched {len(budgets)} budgets")
    
    # Display sample
    display_budgets(budgets)
    
    # Generate CSV files
    print("\n" + "=" * 100)
    print("Generating CSV files...")
    print("=" * 100)
    
    success = True
    
    # Generate mapping CSV (unless update-only)
    if not args.update_only:
        if not save_mapping_csv(budgets, args.mapping_file):
            success = False
    
    # Generate update CSV (unless mapping-only)
    if not args.mapping_only:
        if not save_update_csv(budgets, args.default_amount, args.update_file):
            success = False
    
    # Summary
    print("\n" + "=" * 100)
    print("Summary")
    print("=" * 100)
    print(f"Total budgets fetched: {len(budgets)}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    if not args.update_only:
        print(f"\n📋 Mapping CSV: {args.mapping_file}")
        print("   Use for: Reference and record-keeping")
        print("   Contains: ID, Entity Name, Amount, Consumed, Scope, Type")
    
    if not args.mapping_only:
        print(f"\n📝 Update CSV: {args.update_file}")
        print("   Use for: Running budget updates with update_budgets_final.py")
        print(f"   Contains: Entity Name, Budget ID, New Amount (default: ${args.default_amount})")
        print("\n   Next steps:")
        print(f"   1. Edit {args.update_file} to change amounts if needed")
        print(f"   2. Run: python3 update_budgets_final.py -i {args.update_file} -t TOKEN -e {args.enterprise} --dry-run")
        print(f"   3. Run: python3 update_budgets_final.py -i {args.update_file} -t TOKEN -e {args.enterprise}")
    
    print("\n" + "=" * 100)
    
    if success:
        print("✓ All CSV files generated successfully!")
        sys.exit(0)
    else:
        print("✗ Some files failed to generate")
        sys.exit(1)


if __name__ == "__main__":
    main()
