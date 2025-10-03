# GitLab to GitHub Sync Setup

This document explains how to configure automatic syncing from GitLab to GitHub.

## Prerequisites

1. Access to GitLab repository settings
2. GitHub repository created: `aws-solutions-library-samples/guidance-for-connected-mobility-on-aws`
3. GitHub Personal Access Token with `repo` permissions

## Setup Steps

### 1. Create GitHub Personal Access Token

1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Configure token:
   - **Note**: `GitLab CI Sync`
   - **Expiration**: 90 days (or custom)
   - **Scopes**: Check `repo` (full control of private repositories)
4. Click "Generate token"
5. **Copy the token immediately** (you won't see it again)

### 2. Add Token to GitLab CI/CD Variables

1. Go to your GitLab project
2. Navigate to **Settings → CI/CD → Variables**
3. Click **Add variable**
4. Configure:
   - **Key**: `GITHUB_TOKEN`
   - **Value**: Paste your GitHub token
   - **Type**: Variable
   - **Environment scope**: All
   - **Protect variable**: ✅ (recommended)
   - **Mask variable**: ✅ (recommended)
5. Click **Add variable**

### 3. Verify Configuration

1. Commit and push to `main` branch:
   ```bash
   git add .gitlab-ci.yml
   git commit -m "Add GitHub sync pipeline"
   git push origin main
   ```

2. Check pipeline in GitLab:
   - Go to **CI/CD → Pipelines**
   - Verify `sync_to_github` job runs successfully
   - Check logs for any errors

3. Verify on GitHub:
   - Go to your GitHub repository
   - Confirm latest commit appears
   - Check that commit history is preserved

## How It Works

### Automatic Sync Triggers

The pipeline automatically syncs when:
- ✅ Commits are pushed to `main` branch
- ✅ Version tags are created (e.g., `v1.0.0`, `v2.1.3`)

The pipeline does NOT sync:
- ❌ Feature branches
- ❌ Scheduled pipelines
- ❌ If `GITHUB_TOKEN` is not configured

### What Gets Synced

- All commits on `main` branch
- Full commit history
- All tags matching version pattern (`v*.*.*`)
- Branch structure

### What Does NOT Get Synced

- Feature branches (unless explicitly configured)
- GitLab-specific files (CI/CD configs remain)
- Merge request metadata
- GitLab issues and comments

## Manual Sync (Backup Method)

If CI/CD fails, you can manually sync:

```bash
# Add GitHub remote (one-time setup)
git remote add github https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git

# Sync main branch
git push github main --force

# Sync all tags
git push github --tags --force
```

## Troubleshooting

### Pipeline Fails: "Authentication failed"

**Cause**: Invalid or expired GitHub token

**Solution**:
1. Generate new GitHub token
2. Update `GITHUB_TOKEN` in GitLab CI/CD variables
3. Re-run pipeline

### Pipeline Fails: "Repository not found"

**Cause**: Incorrect GitHub repository URL or insufficient permissions

**Solution**:
1. Verify repository exists on GitHub
2. Check token has `repo` scope
3. Update repository URL in `.gitlab-ci.yml` if needed

### Commits Not Appearing on GitHub

**Cause**: Pipeline only runs on `main` branch

**Solution**:
- Ensure you're pushing to `main` branch
- Check pipeline logs in GitLab CI/CD
- Verify `GITHUB_TOKEN` is configured

### Force Push Concerns

**Note**: Pipeline uses `--force` to ensure sync

**Why**: Prevents conflicts if GitHub has diverged
**Risk**: Overwrites GitHub history with GitLab history
**Mitigation**: GitLab is source of truth, GitHub is mirror

## Customization

### Sync Additional Branches

Edit `.gitlab-ci.yml`:

```yaml
only:
  - main
  - develop  # Add additional branches
  - /^release\/.*$/  # Or branch patterns
```

### Filter Sensitive Files

Add to `.gitlab-ci.yml` script section:

```yaml
script:
  # Remove sensitive files before sync
  - rm -f deployment/config.json
  - rm -rf .aws/
  - git add -A
  - git commit -m "Remove sensitive files" || true
  
  # Then push to GitHub
  - git push github HEAD:main --force --tags
```

### Change Sync Frequency

Current: Immediate on every commit to `main`

To sync only on tags:

```yaml
only:
  - /^v\d+\.\d+\.\d+$/  # Only version tags
```

## Security Best Practices

1. **Token Expiration**: Set reasonable expiration (90 days recommended)
2. **Token Scope**: Use minimal scope (`repo` only)
3. **Mask Variable**: Always mask `GITHUB_TOKEN` in GitLab
4. **Protect Variable**: Protect variable to limit access
5. **Audit Logs**: Monitor GitHub repository access logs
6. **Rotate Tokens**: Regularly rotate tokens (quarterly)

## Maintenance

### Token Renewal

When token expires:
1. Generate new GitHub token (same process as setup)
2. Update `GITHUB_TOKEN` in GitLab CI/CD variables
3. No code changes needed

### Monitoring

Check sync health:
- Review GitLab pipeline success rate
- Compare commit counts: `git rev-list --count main` on both repos
- Verify latest commit SHA matches

## Support

For issues:
1. Check GitLab pipeline logs
2. Verify GitHub token permissions
3. Test manual sync as backup
4. Contact DevOps team if persistent issues
