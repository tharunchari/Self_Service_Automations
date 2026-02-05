# Bamboo Spot Interruption Monitor & Auto-Retry

Automatically detects and retries Bamboo builds that failed due to AWS Spot instance reclamation.

## 🎯 What This Does

- ✅ Monitors currently failed Bamboo builds
- ✅ Analyzes build logs to identify spot interruptions vs genuine failures
- ✅ Automatically retries ONLY spot-interrupted builds
- ✅ Intelligently tracks retries to prevent duplicates
- ✅ Runs entirely in GitHub Actions (no server needed)

## 📋 Prerequisites

1. **Bamboo Server** - Accessible from internet (or use self-hosted GitHub runner)
2. **Bamboo User** - With permissions to:
   - View build results
   - View build logs
   - Queue builds
3. **GitHub Repository** - To host this workflow
4. **Bamboo API Token** - Generated from your Bamboo profile

## 🚀 Setup Instructions

### Step 1: Create Bamboo API Token

1. Login to your Bamboo server
2. Click your profile icon (top right) → **Profile**
3. Click **Personal access tokens** (left menu)
4. Click **Create token**
5. Give it a name: `GitHub-Spot-Monitor`
6. Click **Create**
7. **COPY THE TOKEN** - you'll need it in next step

### Step 2: Add GitHub Secrets

1. Go to your GitHub repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add these three secrets:

| Secret Name | Value | Example |
|------------|-------|---------|
| `BAMBOO_URL` | Your Bamboo server URL | `https://bamboo.yourcompany.com` |
| `BAMBOO_USERNAME` | Your Bamboo username | `john.doe` |
| `BAMBOO_API_TOKEN` | Token from Step 1 | `MjQyNzg5ODE2NTMw...` |

**Important:** 
- `BAMBOO_URL` should NOT have trailing slash
- Token is NOT the same as your password

### Step 3: Create Repository Files

Create these 3 files in your repository:

```
your-repo/
├── .github/
│   └── workflows/
│       └── bamboo-spot-monitor.yml
├── bamboo_spot_monitor.py
└── README.md
```

Copy the content from the provided files above.

### Step 4: Commit and Push

```bash
git add .
git commit -m "Add Bamboo spot interruption monitor"
git push origin main
```

### Step 5: Run First Test (Dry Run)

1. Go to your repository on GitHub
2. Click **Actions** tab
3. Click **"Bamboo Spot Interruption Monitor"** in the left sidebar
4. Click **"Run workflow"** button (top right)
5. Configure options:
   - `max_results`: **100** (default)
   - `dry_run`: **true** (default - LIST ONLY)
6. Click **"Run workflow"**

### Step 6: View Results

1. Wait for workflow to complete (~2-5 minutes)
2. Click on the workflow run
3. Click **"monitor-and-retry"** job
4. Expand **"Run Bamboo Spot Monitor"** step
5. Review the output:
   - See which builds are spot interruptions
   - See which are genuine failures
   - Verify detection is working correctly

### Step 7: Download Reports

1. Scroll down to **Artifacts** section
2. Download:
   - `retry-state` - State tracking file
   - `bamboo-spot-report-XXX` - Detailed JSON report

### Step 8: Enable Active Mode

Once you've verified detection works correctly:

1. Go to **Actions** → **Bamboo Spot Interruption Monitor**
2. Click **"Run workflow"**
3. Set `dry_run`: **false**
4. Click **"Run workflow"**

**Now it will actually retry builds!**

## 🎛️ Usage

### Manual Run

**Actions** → **Bamboo Spot Interruption Monitor** → **Run workflow**

### Options

**max_results** (default: 100)
- How many failed builds to check
- Increase if you have many failures
- Max: 500

**dry_run** (default: true)
- `true` = List only, no retries (safe for testing)
- `false` = Actually retry builds

### Recommended Workflow

1. **First run:** `dry_run: true` - Verify detection
2. **Second run:** `dry_run: false` - Enable retries
3. **Subsequent runs:** `dry_run: false` - Continuous monitoring

## 📊 Understanding the Output

### Sample Output

```
📈 SUMMARY REPORT
================================================================================
Run Mode: 🔍 DRY RUN (List Only)
Total Failed Builds: 25
🔴 Spot Interruptions Detected: 8
✓ Genuine Failures: 15
⏭️  Already Retried (Skipped): 2
📋 Would Retry: 8

📊 Spot Interruption Rate: 32.0%

🔴 SPOT-INTERRUPTED BUILDS:
================================================================================

  📋 MyApp-CI-Build #1234
     Plan Key: PROJ-BUILD
     Build URL: https://bamboo.company.com/browse/PROJ-BUILD-1234
     Keywords: Agent disconnected, Lost remote agent
```

### Status Icons

- 🔴 **Spot Interruption Detected** - Will be retried
- ✓ **Genuine Failure** - Won't be retried (needs code fix)
- ⏭️ **Already Retried** - Skipped (already processed before)
- ⚠️ **No Logs Available** - Can't determine (skipped)

### Dry Run vs Active Mode

**Dry Run (`dry_run: true`):**
```
Action: 📋 DRY RUN - Would retry (not actually triggered)
```
- Shows what WOULD happen
- Doesn't actually retry anything
- Safe for testing

**Active Mode (`dry_run: false`):**
```
Action: 🔄 Triggering rebuild...
✓ Rebuild triggered successfully!
```
- Actually queues new builds in Bamboo
- Updates retry tracking state

## ⚙️ Customization

### Add Custom Keywords

If your Bamboo shows different error messages for spot interruptions, edit `bamboo_spot_monitor.py`:

```python
SPOT_INTERRUPTION_KEYWORDS = [
    "Agent disconnected",
    "Lost remote agent",
    # Add your custom keywords here:
    "Your custom error message",
    "Another error pattern",
]
```

**How to find keywords:**
1. Look at failed build logs in Bamboo
2. Find common error messages when spot instances are reclaimed
3. Add those messages to the list

### Change State Retention

Edit line in `bamboo_spot_monitor.py`:

```python
self.retry_state.cleanup_old_entries(days=7)  # Change to 14, 30, etc.
```

## 🔧 Troubleshooting

### Problem: "Missing required environment variables"

**Solution:** Check GitHub Secrets are set correctly:
- Repository → Settings → Secrets and variables → Actions
- Verify: `BAMBOO_URL`, `BAMBOO_USERNAME`, `BAMBOO_API_TOKEN`

### Problem: "Failed to fetch builds: 401 Unauthorized"

**Solution:** 
- API token might be expired - generate new one
- Username might be wrong - verify in Bamboo
- User might lack permissions - check Bamboo permissions

### Problem: "Failed to fetch builds: Connection refused"

**Solution:**
- Check `BAMBOO_URL` is correct
- Ensure Bamboo is accessible from internet
- Or use self-hosted GitHub Actions runner

### Problem: All builds marked as "Already Retried"

**Solution:** This is correct if you ran the workflow multiple times. The workflow prevents infinite retry loops. 

To reset:
1. Go to previous workflow run
2. Delete `retry-state` artifact
3. Run workflow again

### Problem: Not detecting spot interruptions

**Solution:**
1. Check actual error messages in Bamboo logs
2. Add custom keywords to `SPOT_INTERRUPTION_KEYWORDS`
3. Run dry run to test detection

### Problem: Builds still failing after retry

**This is expected!** The workflow only retries once. If the retry also hits spot interruption, it will show as "Already Retried" in next run.

**Manual intervention needed if:**
- Same build retried multiple times and still failing
- Might be a persistent infrastructure issue
- Check Bamboo agent configuration

## 📈 Best Practices

### 1. Start with Dry Run
Always test with `dry_run: true` first to verify detection

### 2. Run Regularly
Run every 15-30 minutes to catch new failures quickly

### 3. Monitor Reports
Download and review JSON reports to track spot interruption trends

### 4. Adjust Keywords
Customize keywords based on your actual error messages

### 5. Set Appropriate max_results
- Small team: 50-100
- Large team: 200-300
- Many failures: 400-500

## 🔒 Security

- ✅ All credentials stored in GitHub Secrets (encrypted)
- ✅ Tokens never logged or exposed
- ✅ Read-only access to build results
- ✅ Only queues new builds (doesn't modify existing)

## 📄 Files Generated

### retry-state.json
- Persistent state across workflow runs
- Tracks which builds were retried
- Prevents duplicate retries
- Auto-uploaded to GitHub Artifacts

### bamboo-spot-report-TIMESTAMP.json
- Detailed report of each run
- All builds analyzed
- Categorizations and actions
- Downloadable from Artifacts

## 🤝 Support

If you need help:
1. Check Troubleshooting section above
2. Review workflow logs in GitHub Actions
3. Check Bamboo server logs
4. Verify API permissions in Bamboo

## 📝 License

This workflow is provided as-is for internal use.
