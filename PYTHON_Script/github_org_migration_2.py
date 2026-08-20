import os
import subprocess
from github import Github

# ==== CONFIGURATION ====
SOURCE_ORG = "vitechsystems"
TARGET_ORG = "Majesco-V3locity-Testing"

SOURCE_TOKEN = ""
TARGET_TOKEN = ""


# Temporary working directory for logs or intermediate files
WORK_DIR = "/u01/github_org_migration/"

# Repositories to exclude from migration if any
EXCLUDE_REPOS = [
]

# ==== SETUP ====
os.makedirs(WORK_DIR, exist_ok=True)
os.chdir(WORK_DIR)

# Connect to GitHub
src = Github(SOURCE_TOKEN)
src_org = src.get_organization(SOURCE_ORG)

# ==== MIGRATION USING GEI ====
for repo in src_org.get_repos():
    if repo.name in EXCLUDE_REPOS:
        print(f"?? Skipping excluded repository: {repo.name}")
        continue

    print(f"\n?? Starting GEI migration for repository: {repo.name}")

    # Run GitHub Enterprise Importer (GEI) command
    try:
        subprocess.run([
            "gh", "gei", "migrate-repo",
            "--github-source-org", SOURCE_ORG,
            "--source-repo", repo.name,
            "--github-target-org", TARGET_ORG,
            "--target-repo", repo.name,
            "--github-source-pat", SOURCE_TOKEN,
            "--github-target-pat", TARGET_TOKEN,
            "--target-repo-visibility", "private",
        ], check=True)
        print(f"?? GEI migration completed successfully for {repo.name}")

    except subprocess.CalledProcessError as e:
        print(f"?? GEI migration failed for {repo.name}: {e}")

print("\n? All GEI migrations completed! ??")