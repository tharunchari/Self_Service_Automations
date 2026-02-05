#!/usr/bin/env python3
"""
Bamboo Spot Instance Interruption Monitor & Auto-Retry
- Checks currently failed builds (not historical)
- Identifies spot interruptions
- Intelligently tracks retried builds to avoid duplicates
- Can run in dry-run mode (list only) or active mode (actually retry)
"""

import requests
import json
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional

# Configuration from environment variables
BAMBOO_URL = os.environ.get('BAMBOO_URL', '').rstrip('/')
USERNAME = os.environ.get('BAMBOO_USERNAME', '')
API_TOKEN = os.environ.get('BAMBOO_API_TOKEN', '')
USE_BEARER = os.environ.get('USE_BEARER_AUTH', 'true').lower() == 'true'
MAX_RESULTS = int(os.environ.get('MAX_RESULTS', '100'))
RECENT_HOURS = int(os.environ.get('RECENT_HOURS', '24'))
DRY_RUN = os.environ.get('DRY_RUN', 'true').lower() == 'true'

STATE_FILE = 'retry-state.json'
REPORT_FILE = f'bamboo-spot-report-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'

# Spot interruption keywords - CUSTOMIZE THESE BASED ON YOUR ERROR MESSAGES
SPOT_INTERRUPTION_KEYWORDS = [
    "Agent disconnected",
    "Lost remote agent",
    "RemoteException",
    "Connection refused",
    "Agent went offline",
    "java.rmi.RemoteException",
    "Connection reset",
    "Elastic Bamboo agent has been terminated",
    "elasticbamboo",
    "Agent is not responding",
    "Remote agent connection lost",
    "Agent shutdown",
    "spot instance",
    "instance termination",
    "AWS EC2 spot",
    "exit 1"  # For testing
]


class RetryState:
    """Manages state of retried builds across workflow runs"""
    
    def __init__(self):
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """Load previous retry state"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    print(f"✓ Loaded previous state: {len(state.get('retried_builds', {}))} tracked builds")
                    return state
            except Exception as e:
                print(f"⚠️  Could not load previous state: {e}")
        
        return {
            'retried_builds': {},
            'last_run': None
        }
    
    def save_state(self):
        """Save current retry state"""
        self.state['last_run'] = datetime.now().isoformat()
        
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)
        
        print(f"✓ State saved: {len(self.state['retried_builds'])} tracked builds")
    
    def was_retried(self, build_key: str, build_time: str) -> bool:
        """Check if this specific build was already retried"""
        if build_key not in self.state['retried_builds']:
            return False
        
        retry_info = self.state['retried_builds'][build_key]
        
        if retry_info.get('original_build_time') == build_time:
            return True
        
        return False
    
    def mark_as_retried(self, build_key: str, build_time: str):
        """Mark a build as retried"""
        if build_key not in self.state['retried_builds']:
            self.state['retried_builds'][build_key] = {
                'retry_count': 0,
                'original_build_time': build_time,
                'first_retry': None
            }
        
        retry_info = self.state['retried_builds'][build_key]
        retry_info['retry_count'] += 1
        retry_info['last_retry_time'] = datetime.now().isoformat()
        retry_info['original_build_time'] = build_time
        
        if retry_info.get('first_retry') is None:
            retry_info['first_retry'] = datetime.now().isoformat()
    
    def get_retry_count(self, build_key: str) -> int:
        """Get number of times this build was retried"""
        return self.state['retried_builds'].get(build_key, {}).get('retry_count', 0)
    
    def cleanup_old_entries(self, days: int = 7):
        """Remove entries older than X days"""
        cutoff = datetime.now() - timedelta(days=days)
        to_remove = []
        
        for build_key, info in self.state['retried_builds'].items():
            last_retry = info.get('last_retry_time')
            if last_retry:
                try:
                    retry_time = datetime.fromisoformat(last_retry)
                    if retry_time < cutoff:
                        to_remove.append(build_key)
                except:
                    pass
        
        for build_key in to_remove:
            del self.state['retried_builds'][build_key]
        
        if to_remove:
            print(f"🧹 Cleaned up {len(to_remove)} old entries (>{days} days)")


class BambooSpotMonitor:
    def __init__(self):
        if not all([BAMBOO_URL, API_TOKEN]):
            print("❌ ERROR: Missing required environment variables")
            print("   Required: BAMBOO_URL, BAMBOO_API_TOKEN")
            sys.exit(1)
        
        self.session = requests.Session()
        
        if USE_BEARER:
            print("🔑 Using Bearer token authentication")
            self.session.headers.update({
                'Authorization': f'Bearer {API_TOKEN}',
                'Accept': 'application/json'
            })
        else:
            print("🔑 Using Basic authentication")
            if not USERNAME:
                print("⚠️  WARNING: BAMBOO_USERNAME not set, using token as password")
                self.session.auth = ('', API_TOKEN)
            else:
                self.session.auth = (USERNAME, API_TOKEN)
            self.session.headers.update({'Accept': 'application/json'})
        
        self.retry_state = RetryState()
        
        self.results = {
            'run_time': datetime.now().isoformat(),
            'dry_run': DRY_RUN,
            'total_failed': 0,
            'spot_interruptions': 0,
            'genuine_failures': 0,
            'already_retried': 0,
            'newly_retried': 0,
            'retry_failed': 0,
            'no_logs': 0,
            'builds': []
        }
    
    def get_current_failed_builds(self) -> List[Dict]:
        """Get failed builds from last X hours - OPTIMIZED"""
        print(f"🔍 Fetching failed builds from last {RECENT_HOURS} hours...")
        print(f"   Checking up to {MAX_RESULTS} recent builds...")
        
        try:
            # Fetch ALL recent builds (not filtered by state) - they come sorted by date
            url = f"{BAMBOO_URL}/rest/api/latest/result"
            params = {
                'max-results': MAX_RESULTS,
                'expand': 'results.result',
                'start-index': 0
            }
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            all_results = data.get('results', {}).get('result', [])
            
            print(f"✓ Fetched {len(all_results)} recent builds from Bamboo")
            
            # Calculate cutoff time
            cutoff_time = datetime.now() - timedelta(hours=RECENT_HOURS)
            
            # Filter for failed builds within time window
            recent_failed = []
            skipped_old = 0
            
            for result in all_results:
                # Skip if not failed
                build_state = result.get('buildState', '')
                if build_state != 'Failed':
                    continue
                
                # Parse build completion time
                build_time_str = result.get('buildCompletedTime', '')
                if not build_time_str:
                    continue
                
                try:
                    # Parse: "2026-02-05T08:08:41.050-05:00"
                    # Extract just the datetime part: "2026-02-05T08:08:41"
                    datetime_part = build_time_str.split('.')[0]  # Remove milliseconds
                    
                    # Handle timezone offset (e.g., "-05:00")
                    if '+' in datetime_part:
                        datetime_part = datetime_part.split('+')[0]
                    # Count hyphens to detect timezone: YYYY-MM-DDTHH:MM:SS-05:00 has 4 hyphens
                    elif datetime_part.count('-') > 2:
                        # Split from the right, take only date/time part
                        datetime_part = datetime_part.rsplit('-', 1)[0]
                    
                    # Parse to datetime
                    build_time = datetime.fromisoformat(datetime_part)
                    
                    # Compare (both are naive datetime, no timezone)
                    if build_time > cutoff_time:
                        recent_failed.append(result)
                    else:
                        skipped_old += 1
                        # Don't break - API might not be perfectly sorted
                        
                except Exception as e:
                    print(f"⚠️  Could not parse time for {result.get('key')}: {build_time_str} - Error: {e}")
                    # Include it if we can't parse (safer)
                    recent_failed.append(result)
            
            print(f"✓ Found {len(recent_failed)} FAILED builds from last {RECENT_HOURS} hours")
            if skipped_old > 0:
                print(f"   (Skipped {skipped_old} older failed builds)")
            
            if len(recent_failed) == 0:
                print(f"💡 No recent failures found.")
                print(f"   - Try increasing RECENT_HOURS (current: {RECENT_HOURS})")
                print(f"   - Or increase MAX_RESULTS (current: {MAX_RESULTS})")
                print(f"   - Or wait for next spot interruption to occur")
            
            return recent_failed
            
        except requests.exceptions.RequestException as e:
            print(f"❌ ERROR: Failed to fetch builds: {e}")
            sys.exit(1)
    
    def get_build_logs(self, build_key: str) -> tuple:
        """Get build logs, returns (logs, success) - FAST TIMEOUT"""
        try:
            url = f"{BAMBOO_URL}/rest/api/latest/result/{build_key}.json"
            params = {'expand': 'logEntries'}
            
            # FAST TIMEOUT: 3 seconds instead of 30
            response = self.session.get(url, params=params, timeout=3)
            response.raise_for_status()
            
            data = response.json()
            log_entries = data.get('logEntries', {}).get('logEntry', [])
            
            if not log_entries:
                # Try alternative endpoint - also fast timeout
                log_url = f"{BAMBOO_URL}/rest/api/latest/result/{build_key}/log"
                log_response = self.session.get(log_url, timeout=3)
                
                if log_response.status_code == 200:
                    return log_response.text, True
                
                return "", False
            
            full_log = '\n'.join([entry.get('log', '') for entry in log_entries])
            return full_log, True
            
        except Exception:
            # Fail fast and silently
            return "", False
    
    def is_spot_interruption(self, logs: str) -> tuple:
        """
        Check if failure was due to spot interruption
        Returns: (is_spot_interruption, matched_keywords)
        """
        if not logs:
            return False, []
        
        matched_keywords = []
        logs_lower = logs.lower()
        
        for keyword in SPOT_INTERRUPTION_KEYWORDS:
            if keyword.lower() in logs_lower:
                matched_keywords.append(keyword)
        
        return len(matched_keywords) > 0, matched_keywords
    
    def trigger_rebuild(self, plan_key: str) -> bool:
        """Trigger a rebuild of the plan"""
        try:
            url = f"{BAMBOO_URL}/rest/api/latest/queue/{plan_key}"
            
            response = self.session.post(url, timeout=10)
            response.raise_for_status()
            
            return True
            
        except Exception as e:
            print(f"   ❌ Failed to trigger rebuild: {e}")
            return False
    
    def process_builds(self, failed_builds: List[Dict]):
        """Process all failed builds"""
        total = len(failed_builds)
        
        if total == 0:
            print("\n✓ No recent failed builds found!")
            return
        
        print(f"\n📊 Analyzing {total} failed builds...")
        print("=" * 100)
        
        for idx, result in enumerate(failed_builds, 1):
            build_key = result.get('key', 'UNKNOWN')
            plan_key = result.get('plan', {}).get('key', 'UNKNOWN')
            plan_name = result.get('plan', {}).get('name', 'Unknown Plan')
            build_number = result.get('buildNumber', 'N/A')
            build_time = result.get('buildCompletedTime', 'N/A')
            build_reason = result.get('buildReason', 'Unknown')
            
            print(f"\n[{idx}/{total}] {plan_name} #{build_number}")
            print(f"         Build Key: {build_key}")
            print(f"         Plan Key: {plan_key}")
            print(f"         Completed: {build_time}")
            
            # Check if already retried
            if self.retry_state.was_retried(build_key, build_time):
                retry_count = self.retry_state.get_retry_count(build_key)
                print(f"         Status: ⏭️  ALREADY RETRIED (retry count: {retry_count})")
                
                self.results['already_retried'] += 1
                self.results['builds'].append({
                    'build_key': build_key,
                    'plan_key': plan_key,
                    'plan_name': plan_name,
                    'build_number': build_number,
                    'status': 'already_retried',
                    'retry_count': retry_count,
                    'build_url': f"{BAMBOO_URL}/browse/{build_key}"
                })
                print("-" * 100)
                continue
            
            # Get logs
            logs, has_logs = self.get_build_logs(build_key)
            
            if not has_logs:
                print(f"         Status: ⚠️  NO LOGS AVAILABLE - Skipping")
                self.results['no_logs'] += 1
                self.results['builds'].append({
                    'build_key': build_key,
                    'plan_key': plan_key,
                    'plan_name': plan_name,
                    'build_number': build_number,
                    'status': 'no_logs',
                    'build_url': f"{BAMBOO_URL}/browse/{build_key}"
                })
                print("-" * 100)
                continue
            
            # Check if spot interruption
            is_spot, keywords = self.is_spot_interruption(logs)
            
            if is_spot:
                print(f"         Status: 🔴 SPOT INTERRUPTION DETECTED")
                print(f"         Keywords: {', '.join(keywords[:3])}{'...' if len(keywords) > 3 else ''}")
                
                # Retry logic
                if DRY_RUN:
                    print(f"         Action: 📋 DRY RUN - Would retry (not actually triggered)")
                    retry_status = 'would_retry'
                else:
                    print(f"         Action: 🔄 Triggering rebuild...")
                    if self.trigger_rebuild(plan_key):
                        print(f"         ✓ Rebuild triggered successfully!")
                        self.retry_state.mark_as_retried(build_key, build_time)
                        retry_status = 'retried'
                        self.results['newly_retried'] += 1
                    else:
                        retry_status = 'retry_failed'
                        self.results['retry_failed'] += 1
                
                self.results['spot_interruptions'] += 1
                self.results['builds'].append({
                    'build_key': build_key,
                    'plan_key': plan_key,
                    'plan_name': plan_name,
                    'build_number': build_number,
                    'build_time': build_time,
                    'status': retry_status,
                    'matched_keywords': keywords,
                    'build_url': f"{BAMBOO_URL}/browse/{build_key}"
                })
            else:
                print(f"         Status: ✓ Genuine build failure (not spot-related)")
                self.results['genuine_failures'] += 1
                self.results['builds'].append({
                    'build_key': build_key,
                    'plan_key': plan_key,
                    'plan_name': plan_name,
                    'build_number': build_number,
                    'status': 'genuine_failure',
                    'build_url': f"{BAMBOO_URL}/browse/{build_key}"
                })
            
            self.results['total_failed'] += 1
            print("-" * 100)
    
    def print_summary(self):
        """Print summary report"""
        print("\n" + "=" * 100)
        print("📈 SUMMARY REPORT")
        print("=" * 100)
        print(f"Run Mode: {'🔍 DRY RUN (List Only)' if DRY_RUN else '🔄 ACTIVE MODE (Actually Retrying)'}")
        print(f"Total Failed Builds: {self.results['total_failed']}")
        print(f"🔴 Spot Interruptions Detected: {self.results['spot_interruptions']}")
        print(f"✓ Genuine Failures: {self.results['genuine_failures']}")
        print(f"⏭️  Already Retried (Skipped): {self.results['already_retried']}")
        print(f"⚠️  No Logs Available: {self.results['no_logs']}")
        
        if DRY_RUN:
            print(f"📋 Would Retry: {self.results['spot_interruptions']}")
        else:
            print(f"✓ Successfully Retried: {self.results['newly_retried']}")
            print(f"❌ Retry Failed: {self.results['retry_failed']}")
        
        if self.results['total_failed'] > 0:
            spot_rate = (self.results['spot_interruptions'] / self.results['total_failed']) * 100
            print(f"\n📊 Spot Interruption Rate: {spot_rate:.1f}%")
        
        # List spot-interrupted builds
        print("\n" + "=" * 100)
        print("🔴 SPOT-INTERRUPTED BUILDS:")
        print("=" * 100)
        
        spot_builds = [b for b in self.results['builds'] 
                      if b['status'] in ['would_retry', 'retried', 'retry_failed']]
        
        if spot_builds:
            for build in spot_builds:
                status_emoji = {
                    'would_retry': '📋',
                    'retried': '✓',
                    'retry_failed': '❌'
                }.get(build['status'], '?')
                
                print(f"\n  {status_emoji} {build['plan_name']} #{build['build_number']}")
                print(f"     Plan Key: {build['plan_key']}")
                print(f"     Build URL: {build['build_url']}")
                if 'matched_keywords' in build:
                    keywords = build['matched_keywords'][:3]
                    print(f"     Keywords: {', '.join(keywords)}{'...' if len(build['matched_keywords']) > 3 else ''}")
        else:
            print("  None!")
        
        # List already retried builds
        if self.results['already_retried'] > 0:
            print("\n" + "=" * 100)
            print("⏭️  BUILDS ALREADY RETRIED (Skipped This Run):")
            print("=" * 100)
            
            retried_builds = [b for b in self.results['builds'] if b['status'] == 'already_retried']
            for build in retried_builds:
                print(f"\n  ⏭️  {build['plan_name']} #{build['build_number']}")
                print(f"     Retry Count: {build.get('retry_count', 0)}")
                print(f"     Build URL: {build['build_url']}")
        
        print("\n" + "=" * 100)
    
    def save_report(self):
        """Save detailed report as JSON"""
        with open(REPORT_FILE, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"✓ Detailed report saved to: {REPORT_FILE}")
    
    def run(self):
        """Main execution"""
        print("\n" + "=" * 100)
        print("🚀 Bamboo Spot Interruption Monitor")
        print("=" * 100)
        print(f"Mode: {'🔍 DRY RUN (List Only)' if DRY_RUN else '🔄 ACTIVE MODE (Will Retry Builds)'}")
        print(f"Bamboo URL: {BAMBOO_URL}")
        print(f"Max Results: {MAX_RESULTS}")
        print(f"Recent Period: Last {RECENT_HOURS} hours")
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 100)
        
        # Cleanup old entries
        self.retry_state.cleanup_old_entries(days=7)
        
        # Get currently failed builds
        failed_builds = self.get_current_failed_builds()
        
        # Process builds
        self.process_builds(failed_builds)
        
        # Print summary
        self.print_summary()
        
        # Save state and report
        self.retry_state.save_state()
        self.save_report()
        
        print("\n" + "=" * 100)
        if DRY_RUN:
            print("✓ Dry run completed! Set dry_run=false to actually retry builds.")
        else:
            print("✓ Active run completed!")
        print("=" * 100)


if __name__ == "__main__":
    monitor = BambooSpotMonitor()
    monitor.run()
