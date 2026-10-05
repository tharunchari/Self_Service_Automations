/**
 * GitHub Workflow Commenter Service
 * Efficiently modifies workflow files using GitHub REST API (no cloning needed)
 */

const https = require('https');

class WorkflowCommentService {
    constructor(token, org) {
        this.token = token;
        this.org = org;
        this.apiBase = 'api.github.com';
        this.rateLimitRemaining = Infinity;
        this.stats = {
            processed: 0,
            modified: 0,
            skipped: 0,
            failed: 0,
            totalFiles: 0
        };
    }

    /**
     * Make authenticated GitHub API request
     */
    async request(method, path, body = null) {
        return new Promise((resolve, reject) => {
            const options = {
                hostname: this.apiBase,
                path: path,
                method: method,
                headers: {
                    'Authorization': `token ${this.token}`,
                    'Accept': 'application/vnd.github.v3.raw+json',
                    'User-Agent': 'WorkflowCommenter/1.0',
                    'X-GitHub-Api-Version': '2022-11-28'
                }
            };

            const req = https.request(options, (res) => {
                let data = '';
                
                // Track rate limiting
                if (res.headers['x-ratelimit-remaining']) {
                    this.rateLimitRemaining = parseInt(res.headers['x-ratelimit-remaining']);
                }

                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    if (res.statusCode >= 400) {
                        reject(new Error(`API Error ${res.statusCode}: ${data}`));
                    } else {
                        resolve({ data, headers: res.headers, statusCode: res.statusCode });
                    }
                });
            });

            req.on('error', reject);
            
            if (body) {
                req.write(JSON.stringify(body));
            }
            req.end();
        });
    }

    /**
     * Fetch all repositories in org with pagination
     */
    async fetchAllRepositories() {
        const repos = [];
        let page = 1;
        const perPage = 100;

        while (true) {
            console.log(`Fetching repos page ${page}...`);
            try {
                const response = await this.request(
                    'GET',
                    `/orgs/${this.org}/repos?page=${page}&per_page=${perPage}&type=all`
                );
                const data = JSON.parse(response.data);
                
                if (!Array.isArray(data) || data.length === 0) break;
                repos.push(...data.map(r => r.name));
                
                if (data.length < perPage) break;
                page++;
            } catch (e) {
                console.error(`Error fetching repos page ${page}:`, e.message);
                break;
            }
        }

        console.log(`Found ${repos.length} repositories`);
        return repos;
    }

    /**
     * Get file content from repository
     */
    async getFileContent(repo, filePath, branch = 'main') {
        try {
            const response = await this.request(
                'GET',
                `/repos/${this.org}/${repo}/contents/${filePath}?ref=${branch}`
            );
            const content = Buffer.from(response.data, 'base64').toString('utf-8');
            return { content, sha: JSON.parse(response.data).sha };
        } catch (e) {
            return null; // File doesn't exist
        }
    }

    /**
     * Get all branches for a repository
     */
    async getBranches(repo) {
        const branches = [];
        let page = 1;

        while (true) {
            try {
                const response = await this.request(
                    'GET',
                    `/repos/${this.org}/${repo}/branches?page=${page}&per_page=100`
                );
                const data = JSON.parse(response.data);
                
                if (!Array.isArray(data) || data.length === 0) break;
                branches.push(...data.map(b => b.name));
                
                if (data.length < 100) break;
                page++;
            } catch (e) {
                console.error(`Error fetching branches for ${repo}:`, e.message);
                break;
            }
        }

        return branches;
    }

    /**
     * Comment out specific patterns in workflow file
     */
    commentOutPatterns(content, options = {}) {
        const { commentPullRequest = true, commentSchedule = true } = options;
        let modified = false;
        let lines = content.split('\n');
        
        lines = lines.map((line, idx) => {
            const trimmed = line.trim();
            
            // Comment pull_request trigger
            if (commentPullRequest && trimmed === 'pull_request') {
                if (!line.trimLeft().startsWith('#')) {
                    return line.replace(/^(\s*)/, '$1#  ');
                }
            }
            
            // Comment schedule section
            if (commentSchedule && trimmed === 'schedule:') {
                if (!line.trimLeft().startsWith('#')) {
                    lines[idx] = line.replace(/^(\s*)/, '$1#  ');
                    modified = true;
                    
                    // Also comment following cron lines
                    let nextIdx = idx + 1;
                    while (nextIdx < lines.length) {
                        const nextLine = lines[nextIdx];
                        const nextTrimmed = nextLine.trim();
                        
                        // Stop if we hit a different top-level key
                        if (nextTrimmed && !nextTrimmed.startsWith('-') && !nextTrimmed.startsWith('cron') &&
                            !nextLine.match(/^\s{2,}/)) {
                            break;
                        }
                        
                        if (nextTrimmed && !nextLine.trimLeft().startsWith('#')) {
                            lines[nextIdx] = nextLine.replace(/^(\s*)/, '$1#  ');
                            modified = true;
                        }
                        nextIdx++;
                    }
                    
                    return lines[idx];
                }
            }
            
            return line;
        });
        
        return {
            content: lines.join('\n'),
            modified: modified
        };
    }

    /**
     * Update file content via GitHub API
     */
    async updateFileContent(repo, filePath, newContent, sha, branch = 'main', message) {
        try {
            const response = await this.request(
                'PUT',
                `/repos/${this.org}/${repo}/contents/${filePath}`,
                {
                    message: message,
                    content: Buffer.from(newContent).toString('base64'),
                    sha: sha,
                    branch: branch
                }
            );
            return { success: true, response: JSON.parse(response.data) };
        } catch (e) {
            throw new Error(`Failed to update ${filePath}: ${e.message}`);
        }
    }

    /**
     * Process a single file in a repo/branch
     */
    async processFile(repo, filePath, branch, options, dryRun = true) {
        try {
            const fileData = await this.getFileContent(repo, filePath, branch);
            
            if (!fileData) {
                return { status: 'skipped', reason: 'File not found' };
            }

            const result = this.commentOutPatterns(fileData.content, options);
            
            if (!result.modified) {
                return { status: 'skipped', reason: 'No changes needed' };
            }

            if (dryRun) {
                return { status: 'success', modified: true, dryRun: true };
            }

            // Actually commit the change
            await this.updateFileContent(
                repo,
                filePath,
                result.content,
                fileData.sha,
                branch,
                options.commitMessage
            );

            return { status: 'success', modified: true };
        } catch (e) {
            return { status: 'error', error: e.message };
        }
    }

    /**
     * Process a single repository
     */
    async processRepository(repo, options) {
        console.log(`\n📦 Processing repository: ${repo}`);
        
        const workflowFiles = options.workflowFiles || ['file1.yml', 'file2.yml', 'file3.yml'];
        const workflowPath = options.workflowPath || '.github/workflows/';
        const specifiedBranches = options.branches ? options.branches.split(',').map(b => b.trim()) : [];
        
        let branches = specifiedBranches;
        
        // If no specific branches provided, fetch all
        if (branches.length === 0) {
            branches = await this.getBranches(repo);
            if (branches.length === 0) {
                return { status: 'error', reason: 'No branches found' };
            }
        }

        const repoResults = {
            repo: repo,
            branches: {},
            totalModified: 0,
            status: 'success'
        };

        // Process each branch
        for (const branch of branches) {
            console.log(`  📌 Branch: ${branch}`);
            repoResults.branches[branch] = {};

            for (const file of workflowFiles) {
                const filePath = workflowPath + file;
                const result = await this.processFile(repo, filePath, branch, options, options.dryRun);
                
                repoResults.branches[branch][file] = result;
                
                if (result.modified) {
                    repoResults.totalModified++;
                }
                
                console.log(`    ${file}: ${result.status}${result.reason ? ' (' + result.reason + ')' : ''}`);
            }
        }

        this.stats.processed++;
        if (repoResults.totalModified > 0) {
            this.stats.modified++;
        }
        
        return repoResults;
    }

    /**
     * Process repositories in batches
     */
    async processBatch(repos, options, batchSize = 10) {
        const results = [];
        
        for (let i = 0; i < repos.length; i += batchSize) {
            const batch = repos.slice(i, i + batchSize);
            console.log(`\n🔄 Processing batch ${Math.floor(i / batchSize) + 1}/${Math.ceil(repos.length / batchSize)}`);
            console.log(`   Rate limit remaining: ${this.rateLimitRemaining}`);
            
            // Process batch sequentially to avoid hitting rate limits
            for (const repo of batch) {
                try {
                    const result = await this.processRepository(repo, options);
                    results.push(result);
                } catch (e) {
                    console.error(`Error processing ${repo}:`, e.message);
                    this.stats.failed++;
                    results.push({
                        repo: repo,
                        status: 'error',
                        error: e.message
                    });
                }
                
                // Rate limit handling
                if (this.rateLimitRemaining < 100) {
                    console.warn('⚠️  Approaching rate limit, waiting 60 seconds...');
                    await this.sleep(60000);
                }
            }
        }
        
        return results;
    }

    /**
     * Main execution method
     */
    async execute(options) {
        console.log('🚀 Starting GitHub Workflow Commenter');
        console.log(`Organization: ${this.org}`);
        console.log(`Dry run: ${options.dryRun}`);
        
        try {
            // Fetch repositories
            let repos;
            if (options.useAllRepos) {
                repos = await this.fetchAllRepositories();
            } else {
                repos = options.specificRepos || [];
            }

            if (repos.length === 0) {
                throw new Error('No repositories found');
            }

            console.log(`\n📋 Found ${repos.length} repositories to process`);

            // Process in batches
            const results = await this.processBatch(repos, options, 10);

            return {
                success: true,
                stats: this.stats,
                results: results
            };
        } catch (e) {
            return {
                success: false,
                error: e.message
            };
        }
    }

    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}

// Example usage
async function main() {
    const service = new WorkflowCommentService(
        process.env.GITHUB_TOKEN,
        process.env.ORG_NAME || 'my-org'
    );

    const options = {
        useAllRepos: true,
        workflowFiles: ['file1.yml', 'file2.yml', 'file3.yml'],
        workflowPath: '.github/workflows/',
        branches: '', // Empty = all branches
        dryRun: true,
        commentPullRequest: true,
        commentSchedule: true,
        commitMessage: 'ci: comment out pull_request and schedule triggers'
    };

    const result = await service.execute(options);
    console.log('\n📊 Summary:', result.stats);
    console.log(JSON.stringify(result, null, 2));
}

module.exports = WorkflowCommentService;

// Uncomment to run directly:
// if (require.main === module) {
//     main().catch(console.error);
// }
