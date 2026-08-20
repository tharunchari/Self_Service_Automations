import os
import subprocess
from github import Github

# ==== CONFIGURATION ====
SOURCE_ORG = "vitechsystems"
TARGET_ORG = "Vitech-GHAS-Testing"

SOURCE_TOKEN = ""
TARGET_TOKEN = ""

SOURCE_REPO = "PSERS_PRODUCT_L5,METLIFE_NEXTGEN_L5,METLIFE_PRODUCT_L5,METLIFE_MARKETPLACE_LAMBDA,METLIFE_FACTORY_7.0,METLIFE,CPF,aom-workbench,UPP"

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
for repo_name in SOURCE_REPO.split(","):
    repo_name = repo_name.strip()

    if repo_name in EXCLUDE_REPOS:
        print(f"?? Skipping excluded repository: {repo_name}")
        continue

    print(f"\n?? Starting GEI migration for repository: {repo_name}")

    # Run GitHub Enterprise Importer (GEI) command
    try:
        subprocess.run([
            "gh", "gei", "migrate-repo",
            "--github-source-org", SOURCE_ORG,
            "--source-repo", repo_name,
            "--github-target-org", TARGET_ORG,
            "--target-repo", repo_name,
            "--github-source-pat", SOURCE_TOKEN,
            "--github-target-pat", TARGET_TOKEN,
            "--target-repo-visibility", "private",
        ], check=True)
        print(f"?? GEI migration completed successfully for {repo_name}")

    except subprocess.CalledProcessError as e:
        print(f"?? GEI migration failed for {repo_name}: {e}")

print("\n? All GEI migrations completed! ??")