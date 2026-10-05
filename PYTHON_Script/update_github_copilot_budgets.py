#!/usr/bin/env python3
"""
GitHub Enterprise Copilot Budget Updater - Final Version with Budget IDs

Uses Budget IDs (not entity names) to update individual user budgets.

CSV Formats Supported:
  Option 1: Budget Entity Name,New Budget Amount
            MMuvvala_vitech,80
            
  Option 2: Budget Entity Name,Budget ID,New Budget Amount
            MMuvvala_vitech,6099d204-3fc0-4325-89f7-e0512b07b791,80

Usage:
    python3 update_github_copilot_budgets.py -i budgets.csv -t <token> -e <enterprise>

Example:
    python3 update_github_copilot_budgets.py -i budget_updates_ready.csv -t ghp_xxx -e vitech
"""

import argparse
import sys
import time
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Install with: pip install requests")
    sys.exit(1)


class GitHubBudgetUpdater:
    """Updates GitHub Copilot budgets using Budget IDs"""
    
    BASE_URL = "https://api.github.com"
    API_VERSION = "2026-03-10"
    
    def __init__(self, token: str, enterprise: str, delay_ms: int = 500, dry_run: bool = False):
        self.token = token
        self.enterprise = enterprise
        self.delay_seconds = delay_ms / 1000.0
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
            "Content-Type": "application/json"
        })
        self.results = []
        self.entity_to_id_map = {}
    
    def fetch_budget_ids(self) -> bool:
        """Fetch budget IDs from GitHub"""
        print("\nFetching budget IDs...")
        
        url = f"{self.BASE_URL}/enterprises/{self.enterprise}/settings/billing/budgets"
        page = 1
        total_budgets = 0
        
        while True:
            try:
                response = self.session.get(url, params={"per_page": 100, "page": page}, timeout=10)
                
                if response.status_code != 200:
                    print(f"? Failed: {response.status_code}")
                    return False
                
                data = response.json()
                budgets = data.get('budgets', [])
                
                if not budgets:
                    break
                
                for budget in budgets:
                    entity_name = budget.get('budget_entity_name')
                    budget_id = budget.get('id')
                    if entity_name and budget_id:
                        self.entity_to_id_map[entity_name] = budget_id
                
                total_budgets += len(budgets)
                print(f"  Page {page}: {len(budgets)} budgets")
                
                if not data.get('has_next_page', False):
                    break
                
                page += 1
            
            except Exception as e:
                print(f"? Error: {e}")
                return False
        
        print(f"? Fetched {len(self.entity_to_id_map)} budgets")
        return True
    
    def read_csv(self, filepath: str) -> List[Tuple[str, str, float]]:
        """
        Read CSV file
        
        Returns: List of (entity_name, budget_id, amount) tuples
        """
        budgets = []
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if not lines:
                    raise ValueError("CSV file is empty")
                
                # Check header
                header = lines[0].strip().split(',')
                has_id_col = 'Budget ID' in lines[0]
                has_amount_col = 'New Budget Amount' in lines[0]
                
                if not has_amount_col:
                    raise ValueError("CSV must have 'New Budget Amount' column")
                
                # Parse CSV
                reader = csv.DictReader(lines)
                for row_num, row in enumerate(reader, start=2):
                    entity = row.get('Budget Entity Name', '').strip()
                    budget_id = row.get('Budget ID', '').strip()
                    amount_str = row.get('New Budget Amount', '').strip()
                    
                    if entity and amount_str:
                        try:
                            amount = float(amount_str)
                            budgets.append((entity, budget_id, amount))
                        except ValueError:
                            print(f"  Row {row_num}: Invalid amount '{amount_str}'")
            
            return budgets
        
        except Exception as e:
            print(f"ERROR reading CSV: {e}")
            sys.exit(1)
    
    def update_budget(self, entity_name: str, budget_id: str, amount: float) -> Dict:
        """Update a budget"""
        
        # Use provided ID, or look it up
        if budget_id:
            bid = budget_id
        else:
            bid = self.entity_to_id_map.get(entity_name)
        
        if not bid:
            return {
                "entity": entity_name,
                "budget_id": "NOT_FOUND",
                "amount": amount,
                "status": "?",
                "code": 0,
                "message": "Entity not found",
                "timestamp": datetime.now().isoformat()
            }
        
        url = f"{self.BASE_URL}/enterprises/{self.enterprise}/settings/billing/budgets/{bid}"
        payload = {"budget_amount": amount}
        
        try:
            if self.dry_run:
                return {
                    "entity": entity_name,
                    "budget_id": bid,
                    "amount": amount,
                    "status": "?",
                    "code": 200,
                    "message": "Dry-run OK",
                    "timestamp": datetime.now().isoformat()
                }
            
            response = self.session.patch(url, json=payload, timeout=10)
            
            return {
                "entity": entity_name,
                "budget_id": bid,
                "amount": amount,
                "status": "?" if response.status_code == 200 else "?",
                "code": response.status_code,
                "message": "Success" if response.status_code == 200 else response.text[:100],
                "timestamp": datetime.now().isoformat()
            }
        
        except Exception as e:
            return {
                "entity": entity_name,
                "budget_id": bid,
                "amount": amount,
                "status": "?",
                "code": 0,
                "message": str(e)[:100],
                "timestamp": datetime.now().isoformat()
            }
    
    def update_all(self, budgets: List[Tuple[str, str, float]]) -> Tuple[int, int]:
        """Update all budgets"""
        
        mode = "DRY RUN" if self.dry_run else "LIVE UPDATE"
        print(f"\n{mode}")
        print(f"Total: {len(budgets)} budgets")
        print(f"Delay: {self.delay_seconds*1000:.0f}ms\n")
        
        success = 0
        fail = 0
        
        for idx, (entity, bid, amount) in enumerate(budgets, 1):
            print(f"[{idx:3d}] {entity:40s} ? ${amount:7.2f}... ", end="", flush=True)
            
            result = self.update_budget(entity, bid, amount)
            self.results.append(result)
            
            if result["status"] == "?":
                print(f"?")
                success += 1
            else:
                print(f"? ({result['message'][:30]})")
                fail += 1
            
            if idx < len(budgets):
                time.sleep(self.delay_seconds)
        
        return success, fail
    
    def write_results(self, output_file: str):
        """Write results to CSV"""
        try:
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "Sl No", "Entity Name", "Budget ID", "Amount", "Status", "Message", "Code"
                ])
                writer.writeheader()
                
                for idx, r in enumerate(self.results, 1):
                    writer.writerow({
                        "Sl No": idx,
                        "Entity Name": r.get("entity", ""),
                        "Budget ID": r.get("budget_id", ""),
                        "Amount": r.get("amount", ""),
                        "Status": r.get("status", ""),
                        "Message": r.get("message", ""),
                        "Code": r.get("code", "")
                    })
            
            print(f"\n? Results saved to: {output_file}")
        except Exception as e:
            print(f"ERROR saving results: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Update GitHub Copilot budgets using Budget IDs",
        epilog="""
CSV Formats:
  Option 1: Budget Entity Name,New Budget Amount
            MMuvvala_vitech,80
  
  Option 2: Budget Entity Name,Budget ID,New Budget Amount
            MMuvvala_vitech,6099d204-3fc0-4325-89f7-e0512b07b791,80

Examples:
  python3 update_budgets_final.py -i budgets.csv -t ghp_xxx -e vitech --dry-run
  python3 update_budgets_final.py -i budgets.csv -t ghp_xxx -e vitech
        """
    )
    
    parser.add_argument("-i", "--input", required=True, help="Input CSV file")
    parser.add_argument("-t", "--token", required=True, help="GitHub token")
    parser.add_argument("-e", "--enterprise", required=True, help="Enterprise name")
    parser.add_argument("-d", "--delay", type=int, default=500, help="Delay in ms (default: 500)")
    parser.add_argument("-o", "--output", default="budget_update_results.csv", help="Output file")
    parser.add_argument("--dry-run", action="store_true", help="Test mode")
    
    args = parser.parse_args()
    
    if not Path(args.input).exists():
        print(f"ERROR: File not found: {args.input}")
        sys.exit(1)
    
    print("=" * 90)
    print("GitHub Copilot Budget Updater")
    print("=" * 90)
    
    updater = GitHubBudgetUpdater(args.token, args.enterprise, args.delay, args.dry_run)
    
    # Read CSV
    print(f"\nReading: {args.input}")
    budgets = updater.read_csv(args.input)
    print(f"? Loaded {len(budgets)} budgets from CSV")
    
    # Check if IDs are provided in CSV
    has_ids = any(bid for _, bid, _ in budgets)
    
    if not has_ids:
        print("\nBudget IDs not in CSV, fetching from GitHub...")
        if not updater.fetch_budget_ids():
            print("? Failed to fetch budget IDs")
            sys.exit(1)
    else:
        print("? Budget IDs provided in CSV")
    
    # Update
    success, fail = updater.update_all(budgets)
    
    # Results
    updater.write_results(args.output)
    
    print("\n" + "=" * 90)
    print(f"Total: {success + fail} | Success: {success} | Failed: {fail}")
    if success + fail > 0:
        print(f"Success Rate: {(success/(success+fail)*100):.1f}%")
    print("=" * 90)
    
    if args.dry_run:
        print("\n? Dry-run complete. Run without --dry-run for actual updates.")
    
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
