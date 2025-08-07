# Internal-Tools Repository

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

This repository contains DevOps automation tools and scripts for managing infrastructure, security scanning, repository operations, and enterprise system maintenance at Vitech Systems.

## Working Effectively

### Bootstrap and Validate Environment
- Install required Python dependencies:
  - `pip3 install boto3 requests pandas jira GitPython` -- takes 10-60 seconds depending on cache. NEVER CANCEL. Set timeout to 120+ seconds.
- Validate environment readiness:
  - `python3 --version` -- should show Python 3.12.3
  - `ansible --version` -- should show Ansible 2.18.7
  - `which curl jq git aws` -- all should be available
- Validate script syntax:
  - Python: `cd PYTHON_Script && python3 -m py_compile *.py`
  - Shell: `cd shell_scripts && for f in *.sh; do bash -n "$f"; done`
  - Ansible: `cd Ansible && ansible-playbook --syntax-check *.yml`
  - Workflows: `cd .github/workflows && python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['echo.yml', 'codeql.yml']]"`

### Build and Test Process
There is NO traditional build process. This repository contains automation scripts that are:
- Executed individually via GitHub Actions workflows
- Run manually with appropriate credentials and environment variables
- Validated through syntax checking and dry-run testing

### Testing Approach
- **Python Scripts**: Use syntax checking with `python3 -m py_compile`
- **Shell Scripts**: Use bash syntax checking with `bash -n script.sh`
- **Ansible Playbooks**: Use `ansible-playbook --syntax-check playbook.yml`
- **GitHub Workflows**: Validate YAML syntax with Python yaml module
- **Database Tests**: pgTAP tests run automatically on SQL file changes

### Running Components

#### Python Scripts (PYTHON_Script directory)
- **AWS Operations**: Scripts like `AWS_runners.py`, `Pythonrunner.py` require AWS credentials
- **GitHub Management**: Scripts like `GITHUB_enterprise_users_and_group.py` require GitHub PAT
- **JIRA Integration**: Scripts like `JIRA_PROJECT_ISSUES.py` require JIRA credentials
- Run syntax check: `python3 -m py_compile script_name.py`
- NEVER run scripts without proper credentials - they interact with production systems

#### Shell Scripts (shell_scripts directory)
- **Repository Management**: `git_repo_creation.sh`, `github_repo_names.sh`
- **Security**: `GITGHAS.sh`, `git_branch_protection.sh`
- All scripts require environment variables: `TOKEN`, `ORG_NAME` for GitHub operations
- Test script syntax: `bash -n script_name.sh`
- Example safe test: `cd shell_scripts && bash -n github_repo_names.sh`

#### Ansible Playbooks (Ansible directory)
- **Repository Backup**: `github_backup.yml` syncs repos to AWS CodeCommit
- Requires inventory file and vault variables for credentials
- Syntax check: `ansible-playbook --syntax-check github_backup.yml`
- NEVER run without proper inventory and credentials

#### GitHub Workflows (.github/workflows)
- **Security Scanning**: `codeql.yml`, `Codeql_Sonar_scan.yml`
- **Infrastructure**: EC2 runner management, patching workflows
- **Testing**: `pgtap_tests.yml` for database validation
- Workflows automatically use self-hosted runners on AWS EC2
- Test YAML syntax: `python3 -c "import yaml; yaml.safe_load(open('workflow.yml'))"`

## Validation Scenarios

### CRITICAL: Timing and Timeout Requirements
- **Dependency Installation**: 30-60 seconds. Set timeout to 120+ seconds. NEVER CANCEL.
- **Syntax Validation**: 5-10 seconds per file
- **Ansible Syntax Check**: 10-15 seconds. NEVER CANCEL.
- **Workflow Execution**: Self-hosted runners take 5-10 minutes to provision. NEVER CANCEL builds.

### Manual Validation Requirements
After making any changes to scripts:
1. **Always run syntax validation** for the affected file type
2. **Test script modifications** in a safe environment before committing
3. **Validate YAML structure** for any workflow changes
4. **Check dependencies** are properly imported in Python scripts

### Complete Validation Workflow
```bash
# 1. Validate Python syntax
cd PYTHON_Script
for py_file in *.py; do
    python3 -m py_compile "$py_file" && echo "$py_file: OK" || echo "$py_file: FAILED"
done

# 2. Validate Shell script syntax
cd ../shell_scripts
for sh_file in *.sh; do
    bash -n "$sh_file" && echo "$sh_file: OK" || echo "$sh_file: FAILED"
done

# 3. Validate Ansible playbooks
cd ../Ansible
for yml_file in *.yml; do
    ansible-playbook --syntax-check "$yml_file" && echo "$yml_file: OK" || echo "$yml_file: FAILED"
done

# 4. Validate GitHub workflows
cd ../.github/workflows
python3 -c "
import yaml, os
for f in os.listdir('.'):
    if f.endswith('.yml') or f.endswith('.yaml'):
        try:
            yaml.safe_load(open(f))
            print(f'{f}: OK')
        except Exception as e:
            print(f'{f}: FAILED - {e}')
"
```

## Repository Structure and Navigation

### Key Directories
- **PYTHON_Script/**: AWS automation, GitHub management, JIRA integration scripts
- **shell_scripts/**: GitHub API scripts, repository management utilities  
- **Ansible/**: Infrastructure automation playbooks for backup and sync
- **.github/workflows/**: Extensive CI/CD workflows for security, patching, testing
- **erpprd_patching/**: Ansible role for ERP production system patching
- **Jenkins_Patching/**: Jenkins system maintenance automation
- **Bamboo.agents/**: Bamboo CI agent management

### Frequently Used Files
- `shell_scripts/github_repo_names.sh` - List repositories in GitHub org
- `PYTHON_Script/AWS_runners.py` - Manage EC2 self-hosted runners
- `Ansible/github_backup.yml` - Backup GitHub repos to AWS CodeCommit
- `.github/workflows/codeql.yml` - Security scanning workflow
- `.github/workflows/pgtap_tests.yml` - Database testing workflow

### Common Script Dependencies
- **Python**: boto3, requests, pandas, jira, GitPython
- **Shell**: curl, jq, git, AWS CLI
- **Ansible**: ansible-core 2.18.7, properly configured inventory
- **GitHub Workflows**: Self-hosted runners with AWS credentials

## Security and Credentials

### Required Environment Variables
Scripts typically require:
- `TOKEN` or `GITHUB_PAT` - GitHub Personal Access Token
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` - AWS credentials
- `ORG_NAME` - GitHub organization name (vitechsystems, vitechinfra)

### NEVER:
- Commit credentials or secrets to the repository
- Run scripts against production without understanding their impact
- Cancel long-running AWS operations that provision infrastructure
- Modify workflows without testing YAML syntax first

### ALWAYS:
- Validate script syntax before making changes
- Use syntax checking commands provided in these instructions
- Test changes in non-production environments when possible
- Check that required environment variables are set before running scripts

## Common Commands Reference

### Quick Syntax Validation
```bash
# Python file
python3 -m py_compile filename.py

# Shell script  
bash -n filename.sh

# Ansible playbook
ansible-playbook --syntax-check playbook.yml

# YAML file
python3 -c "import yaml; yaml.safe_load(open('file.yml'))"
```

### Dependency Management
```bash
# Install Python dependencies
pip3 install boto3 requests pandas jira GitPython

# Check available tools
which python3 ansible curl jq git aws

# Verify versions
python3 --version
ansible --version
```

### Repository Operations
```bash
# List repo structure
find . -maxdepth 2 -type d | grep -v "/\." | head -20

# Count scripts by type
find . -name "*.py" | wc -l
find . -name "*.sh" | wc -l  
find . -name "*.yml" -o -name "*.yaml" | wc -l
```

Always run the complete validation workflow above after making any changes to ensure your modifications don't break existing functionality.