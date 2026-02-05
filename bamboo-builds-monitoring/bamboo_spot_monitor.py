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
        
        # LONGER TIMEOUT for large result sets
        timeout = 60 if MAX_RESULTS > 100 else 30
        print(f"   Using {timeout}s timeout for {MAX_RESULTS} results...")
        
        response = self.session.get(url, params=params, timeout=timeout)
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
                # Just extract YYYY-MM-DDTHH:MM:SS (first 19 characters)
                datetime_part = build_time_str[:19]  # "2026-02-05T08:08:41"
                
                # Parse to datetime (naive, no timezone)
                build_time = datetime.fromisoformat(datetime_part)
                
                # Compare directly (both naive datetimes in EST)
                if build_time > cutoff_time:
                    recent_failed.append(result)
                else:
                    skipped_old += 1
                    
            except Exception as e:
                print(f"⚠️  Could not parse time for {result.get('key')}: {build_time_str}")
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
