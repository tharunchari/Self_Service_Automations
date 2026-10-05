#!/usr/bin/env python3
"""
GitHub User Email Fetcher
Fetches email addresses for GitHub users from a CSV file using GitHub API with PAT authentication.

Usage:
    python github_email_fetcher.py --input users.csv --enterprise ENTERPRISE_NAME --token YOUR_PAT_TOKEN

Example:
    python github_email_fetcher.py --input github_users.csv --enterprise mycompany --token ghp_xxxxxxxxxxxxxxxxx
"""

import csv
import sys
import time
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import requests
from urllib.parse import quote

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('github_email_fetcher.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class GitHubEmailFetcher:
    """Fetches email addresses for GitHub users using GitHub API."""
    
    # GitHub API endpoints
    API_BASE_URL = "https://api.github.com"
    
    def __init__(self, pat_token: str, enterprise: str = None):
        """
        Initialize GitHub API client.
        
        Args:
            pat_token: GitHub Personal Access Token
            enterprise: Enterprise name (optional, for context)
        """
        self.pat_token = pat_token
        self.enterprise = enterprise
        self.headers = {
            "Authorization": f"token {pat_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Email-Fetcher"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.rate_limit_remaining = 5000
        self.rate_limit_reset = None
        self.results = []
        self.failed_users = []
        
    def check_rate_limit(self):
        """Check and log current rate limit status."""
        try:
            response = self.session.get(f"{self.API_BASE_URL}/rate_limit")
            if response.status_code == 200:
                data = response.json()
                self.rate_limit_remaining = data['resources']['core']['remaining']
                self.rate_limit_reset = data['resources']['core']['reset']
                reset_time = datetime.fromtimestamp(self.rate_limit_reset)
                logger.info(f"Rate limit: {self.rate_limit_remaining}/5000 (resets at {reset_time})")
                return True
        except Exception as e:
            logger.warning(f"Could not check rate limit: {e}")
        return False
    
    def wait_for_rate_limit_reset(self):
        """Wait if rate limit is exceeded."""
        if self.rate_limit_remaining < 10:
            sleep_time = self.rate_limit_reset - time.time() + 5
            if sleep_time > 0:
                logger.warning(f"Rate limit nearly exceeded. Waiting {sleep_time:.0f} seconds...")
                time.sleep(sleep_time)
    
    def get_user_email(self, username: str) -> Dict[str, Optional[str]]:
        """
        Fetch email address for a GitHub user.
        
        Args:
            username: GitHub username
            
        Returns:
            Dictionary with username, email, and status
        """
        result = {
            "username": username,
            "email": None,
            "public_email": None,
            "status": "pending",
            "error": None
        }
        
        try:
            # Check rate limit before making request
            self.wait_for_rate_limit_reset()
            
            # Get user information
            user_url = f"{self.API_BASE_URL}/users/{quote(username)}"
            response = self.session.get(user_url, timeout=10)
            
            # Update rate limit info from response headers
            if 'X-RateLimit-Remaining' in response.headers:
                self.rate_limit_remaining = int(response.headers['X-RateLimit-Remaining'])
            
            if response.status_code == 404:
                result["status"] = "not_found"
                result["error"] = "User not found"
                return result
            
            if response.status_code != 200:
                result["status"] = "error"
                result["error"] = f"HTTP {response.status_code}"
                return result
            
            user_data = response.json()
            
            # Check for public email
            if user_data.get('email'):
                result["email"] = user_data['email']
                result["public_email"] = user_data['email']
                result["status"] = "success"
            else:
                result["status"] = "no_public_email"
                result["error"] = "No public email on profile"
            
            return result
        
        except requests.exceptions.Timeout:
            result["status"] = "timeout"
            result["error"] = "Request timeout"
            return result
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            return result
    
    def fetch_emails_from_csv(self, csv_file: str, username_column: str = "username") -> List[Dict]:
        """
        Fetch emails for all users in CSV file.
        
        Args:
            csv_file: Path to CSV file with usernames
            username_column: Column name containing GitHub usernames
            
        Returns:
            List of results with usernames and emails
        """
        if not Path(csv_file).exists():
            logger.error(f"CSV file not found: {csv_file}")
            return []
        
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                usernames = [row.get(username_column, '').strip() 
                           for row in reader 
                           if row.get(username_column, '').strip()]
        except Exception as e:
            logger.error(f"Error reading CSV file: {e}")
            return []
        
        logger.info(f"Found {len(usernames)} usernames in CSV file")
        self.check_rate_limit()
        
        # Process users
        for idx, username in enumerate(usernames, 1):
            logger.info(f"[{idx}/{len(usernames)}] Processing: {username}")
            
            result = self.get_user_email(username)
            self.results.append(result)
            
            if result["status"] != "success":
                self.failed_users.append(username)
            
            # Progress update
            if idx % 50 == 0:
                self.check_rate_limit()
            
            # Small delay to be respectful to API
            time.sleep(0.1)
        
        return self.results
    
    def save_results_to_csv(self, output_file: str = None) -> str:
        """
        Save results to CSV file.
        
        Args:
            output_file: Path to output CSV file (auto-generated if not provided)
            
        Returns:
            Path to output file
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"github_emails_{timestamp}.csv"
        
        try:
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "username", "email", "public_email", "status", "error"
                ])
                writer.writeheader()
                writer.writerows(self.results)
            
            logger.info(f"Results saved to: {output_file}")
            return output_file
        except Exception as e:
            logger.error(f"Error saving results: {e}")
            return None
    
    def print_summary(self):
        """Print summary of results."""
        if not self.results:
            logger.warning("No results to summarize")
            return
        
        total = len(self.results)
        success = sum(1 for r in self.results if r["status"] == "success")
        no_email = sum(1 for r in self.results if r["status"] == "no_public_email")
        not_found = sum(1 for r in self.results if r["status"] == "not_found")
        errors = sum(1 for r in self.results if r["status"] == "error")
        
        logger.info("\n" + "="*60)
        logger.info("SUMMARY")
        logger.info("="*60)
        logger.info(f"Total users processed: {total}")
        logger.info(f"✓ Emails found: {success}")
        logger.info(f"⚠ No public email: {no_email}")
        logger.info(f"✗ User not found: {not_found}")
        logger.info(f"✗ Errors: {errors}")
        logger.info(f"Success rate: {(success/total*100):.1f}%")
        logger.info("="*60 + "\n")
    



def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch email addresses for GitHub users from CSV file(s)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single file
  python github_email_fetcher.py --input users.csv --token YOUR_PAT_TOKEN
  
  # Single file with enterprise
  python github_email_fetcher.py --input users.csv --enterprise mycompany --token YOUR_PAT_TOKEN --output results.csv
  
  # Multiple enterprises (JSON config)
  python github_email_fetcher.py --config enterprises.json --token YOUR_PAT_TOKEN
  
  # Multiple files as separate arguments
  python github_email_fetcher.py --inputs users.csv users_vitech.csv --enterprises general vitech --token YOUR_PAT_TOKEN
        """
    )
    
    # Single input mode
    parser.add_argument('--input', '-i', default=None,
                       help='Input CSV file with GitHub usernames')
    parser.add_argument('--token', '-t', required=True,
                       help='GitHub Personal Access Token (PAT)')
    parser.add_argument('--enterprise', '-e', default=None,
                       help='Enterprise name (optional, for logging)')
    parser.add_argument('--output', '-o', default=None,
                       help='Output CSV file (auto-generated if not provided)')
    parser.add_argument('--column', '-c', default='username',
                       help='CSV column name containing usernames (default: username)')
    
    # Multiple input mode
    parser.add_argument('--inputs', nargs='+', default=None,
                       help='Multiple input CSV files: --inputs file1.csv file2.csv file3.csv')
    parser.add_argument('--enterprises', nargs='+', default=None,
                       help='Enterprise names for each input: --enterprises name1 name2 name3')
    
    # Config file mode
    parser.add_argument('--config', default=None,
                       help='JSON config file with enterprise mappings')
    

    
    args = parser.parse_args()
    
    # Validate inputs
    if not args.token.startswith(('ghp_', 'ghu_', 'ghs_', 'gho_')):
        logger.warning("⚠ Token format may be invalid. GitHub PATs typically start with ghp_, ghu_, ghs_, or gho_")
    
    # Determine processing mode
    if args.config:
        process_config_file(args.config, args.token, args.column)
    elif args.inputs:
        process_multiple_files(args.inputs, args.enterprises, args.token, args.column)
    elif args.input:
        process_single_file(args.input, args.enterprise, args.token, args.column, args.output)
    else:
        logger.error("Must provide either --input, --inputs, or --config")
        parser.print_help()
        sys.exit(1)


def process_single_file(input_file, enterprise, token, column, output):
    """Process a single CSV file."""
    fetcher = GitHubEmailFetcher(pat_token=token, enterprise=enterprise)
    
    logger.info("Starting GitHub email fetcher...")
    if enterprise:
        logger.info(f"Enterprise: {enterprise}")
    
    fetcher.fetch_emails_from_csv(input_file, username_column=column)
    output_path = fetcher.save_results_to_csv(output)
    fetcher.print_summary()
    
    logger.info(f"✓ Results saved to: {output_path}")
    
    success_count = sum(1 for r in fetcher.results if r["status"] == "success")
    return success_count > 0


def process_multiple_files(input_files, enterprises, token, column):
    """Process multiple CSV files with different enterprises."""
    if not enterprises:
        enterprises = [None] * len(input_files)
    elif len(enterprises) != len(input_files):
        logger.error(f"Number of enterprises ({len(enterprises)}) must match number of files ({len(input_files)})")
        sys.exit(1)
    
    all_results = []
    total_success = 0
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Processing {len(input_files)} files")
    logger.info(f"{'='*60}\n")
    
    for idx, (input_file, enterprise) in enumerate(zip(input_files, enterprises), 1):
        logger.info(f"\n[{idx}/{len(input_files)}] Processing file: {input_file}")
        if enterprise:
            logger.info(f"Enterprise: {enterprise}")
        
        fetcher = GitHubEmailFetcher(pat_token=token, enterprise=enterprise)
        fetcher.fetch_emails_from_csv(input_file, username_column=column)
        
        output_file = f"github_emails_{enterprise}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv" if enterprise else None
        output_path = fetcher.save_results_to_csv(output_file)
        
        fetcher.print_summary()
        
        success_count = sum(1 for r in fetcher.results if r["status"] == "success")
        total_success += success_count
        all_results.append({
            'file': input_file,
            'enterprise': enterprise,
            'output': output_path,
            'total': len(fetcher.results),
            'success': success_count,
            'results': fetcher.results
        })
    
    # Print consolidated summary
    print_consolidated_summary(all_results)
    
    return total_success > 0


def process_config_file(config_file, token, column):
    """Process enterprises from JSON config file."""
    import json
    
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_file}")
        sys.exit(1)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in config file: {config_file}")
        sys.exit(1)
    
    enterprises = config.get('enterprises', [])
    if not enterprises:
        logger.error("No enterprises found in config file")
        sys.exit(1)
    
    input_files = [e.get('input') for e in enterprises]
    enterprise_names = [e.get('name') for e in enterprises]
    
    process_multiple_files(input_files, enterprise_names, token, column)


def print_consolidated_summary(all_results):
    """Print consolidated summary for multiple files."""
    logger.info("\n" + "="*60)
    logger.info("CONSOLIDATED SUMMARY - ALL ENTERPRISES")
    logger.info("="*60)
    
    total_files = len(all_results)
    total_users = sum(r['total'] for r in all_results)
    total_success = sum(r['success'] for r in all_results)
    
    logger.info(f"\nFiles processed: {total_files}")
    logger.info(f"Total users: {total_users}")
    logger.info(f"Total emails found: {total_success}")
    logger.info(f"Overall success rate: {(total_success/total_users*100):.1f}%\n")
    
    logger.info("Per-Enterprise Summary:")
    logger.info("-" * 60)
    
    for result in all_results:
        enterprise = result['enterprise'] or 'General'
        success_rate = (result['success'] / result['total'] * 100) if result['total'] > 0 else 0
        logger.info(f"{enterprise:20} | Users: {result['total']:4} | Emails: {result['success']:4} | Rate: {success_rate:5.1f}%")
    
    logger.info("="*60 + "\n")


if __name__ == "__main__":
    main()
