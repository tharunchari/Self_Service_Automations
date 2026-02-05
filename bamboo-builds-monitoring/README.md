# Bamboo Spot Interruption Monitor - Trial Run

This GitHub Actions workflow monitors Bamboo builds and identifies which failures were caused by AWS Spot instance reclamation vs genuine build failures.

## Setup Instructions

### 1. Add Repository Secrets

Go to your GitHub repository ��� Settings → Secrets and variables → Actions → New repository secret

Add these three secrets:

- `BAMBOO_URL`: Your Bamboo server URL (e.g., `https://bamboo.yourcompany.com`)
- `BAMBOO_USERNAME`: Bamboo username with API access
- `BAMBOO_API_TOKEN`: Bamboo API token (generate from your Bamboo profile)

### 2. Create Files

Create these files in your repository:

- `.github/workflows/bamboo-spot-monitor.yml`
- `bamboo_spot_monitor_trial.py`

### 3. Run the Workflow

**Manual Run:**
1. Go to Actions tab in your GitHub repository
2. Select "Bamboo Spot Interruption Monitor (Trial Run)"
3. Click "Run workflow"

**Automatic Run:**
- Uncomment the schedule section in the workflow to run every 15 minutes

### 4. View Results

After the workflow completes:
1. Go to the workflow run
2. Check the logs for the summary report
3. Download the `bamboo-spot-report-*` artifact for detailed JSON report

## What It Does

✅ Lists all failed builds from the last 24 hours
✅ Analyzes build logs to identify spot interruptions
✅ Categorizes failures into:
  - 🔴 Spot Interruptions (would be retried)
  - ✓ Genuine Failures (would NOT be retried)
  - ⚠️  Unable to Determine (no logs available)
✅ Generates a detailed JSON report
✅ Runs for maximum 15 minutes
✅ **Does NOT trigger any retries** (read-only trial)

## Sample Output
