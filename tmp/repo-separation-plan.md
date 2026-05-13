# Repository Separation Plan: Mac vs 1700

## Current State (Mac)

### Git Remotes
```
origin      = https://github.com/tentenone1/nautilus-trading.git     (fetch + push)
mac-origin  = https://github.com/tentenone1/nautilus-paper-mac.git   (fetch only)
```

### Branch Tracking
```
* main → tracks origin/main (nautilus-trading) ❌ WRONG
```

### Problem
- `git push` on Mac pushes to **nautilus-trading** (production repo)
- Branch `main` tracks the wrong remote
- Mac can accidentally overwrite production code

## Recommended Solution

### 1. Remote Naming Strategy

**Keep it simple and explicit:**
- `origin` → nautilus-paper-mac (Mac's primary repo)
- `production` → nautilus-trading (1700 production repo, fetch only)

This makes `git push` default to the correct repo and makes the production remote clearly labeled as something to be careful with.

### 2. Step-by-Step Implementation (Mac)

#### Phase 1: Backup Current State
```bash
cd ~/workspace/nautilus-trading
git remote -v > /tmp/git-remote-backup.txt
git branch -a > /tmp/git-branches-backup.txt
```

#### Phase 2: Reconfigure Remotes
```bash
# Remove existing remotes
git remote remove origin
git remote remove mac-origin

# Add Mac's repo as origin (primary)
git remote add origin https://github.com/tentenone1/nautilus-paper-mac.git

# Add production repo as fetch-only (renamed for clarity)
git remote add production https://github.com/tentenone1/nautilus-trading.git

# Explicitly disable push to production
git remote set-url --push production DISABLED
```

#### Phase 3: Fix Branch Tracking
```bash
# Update main to track the correct remote
git branch --set-upstream-to=origin/main main
```

#### Phase 4: Verify
```bash
git remote -v
# Should show:
# origin      https://github.com/tentenone1/nautilus-paper-mac.git (fetch)
# origin      https://github.com/tentenone1/nautilus-paper-mac.git (push)
# production  https://github.com/tentenone1/nautilus-trading.git (fetch)
# production  DISABLED (push)

git branch -vv
# Should show:
# * main [origin/main] ...
```

#### Phase 5: Test Push Safety
```bash
# This should work (push to Mac repo)
git push origin main

# This should fail (push to production blocked)
git push production main
# Expected: fatal: 'DISABLED' does not appear to be a git repository
```

### 3. Safety Mechanisms

#### Git-Level Protection (Mac)
```bash
# Add a pre-push hook to prevent accidental pushes to production
cat > .git/hooks/pre-push << 'EOF'
#!/bin/sh
# Prevent pushing to nautilus-trading from Mac
remote="$1"
if [ "$remote" = "production" ]; then
  echo "❌ BLOCKED: Pushing to production (nautilus-trading) from Mac is disabled"
  echo "   Use 1700 server for production deployments"
  exit 1
fi
EOF
chmod +x .git/hooks/pre-push
```

#### GitHub-Level Protection (Production Repo)
- Enable branch protection on `main` in nautilus-trading
- Require PR reviews before merging
- Restrict who can push directly to main
- This provides a safety net even if git config is bypassed

### 4. 1700 Server Considerations

**1700 should:**
- Continue using nautilus-trading as origin
- NOT have any reference to nautilus-paper-mac
- Optionally add branch protection rules on GitHub
- Consider using a different branch name if syncing code between systems (e.g., `mac/paper-trading`)

**If code needs to flow from Mac to 1700:**
- Use GitHub PRs from Mac repo → production repo
- Or use a shared branch with clear naming convention
- Never direct push from Mac to production

### 5. Final State

#### Mac (`~/workspace/nautilus-trading`)
```
origin      → nautilus-paper-mac (fetch + push) ✅
production  → nautilus-trading (fetch only, push blocked) ✅
main        → tracks origin/main ✅
```

#### 1700 (`~/workspace/nautilus-trading`)
```
origin      → nautilus-trading (fetch + push) ✅
main        → tracks origin/main ✅
```

### 6. Emergency Recovery

If something goes wrong:
```bash
# Restore backup
cat /tmp/git-remote-backup.txt
git remote remove origin
git remote remove production
# Re-add from backup

# Or clone fresh:
git clone https://github.com/tentenone1/nautilus-paper-mac.git ~/workspace/nautilus-trading-new
```

## Verification Checklist

- [ ] Mac pushes only to nautilus-paper-mac
- [ ] Mac cannot push to nautilus-trading (git error)
- [ ] Mac can fetch from nautilus-trading if needed
- [ ] 1700 continues to work normally
- [ ] Branch tracking points to correct repo
- [ ] Pre-push hook is active
- [ ] GitHub branch protection is enabled (optional but recommended)
