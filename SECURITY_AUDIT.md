# Security Audit: What Should Be in .env and .gitignore

## Current .gitignore Status

Your current `.gitignore` contains:
```
.env
*.json
*.mp4
requirement.txt
```

## ✅ Complete .env File Template

Based on code analysis, here's what should be in your `.env` file:

### Azure Video Indexer (vi_export_json.py)
```env
# Azure Subscription & Resource Group
AZ_SUBSCRIPTION_ID=your-subscription-id-here
AZ_RESOURCE_GROUP=your-resource-group-name

# Video Indexer Account
VI_ACCOUNT_NAME=your-vi-account-name
VI_LOCATION=your-location (e.g., eastus, westus)
VI_ACCOUNT_ID=your-account-id-uuid
VI_API_SUBSCRIPTION_KEY=your-api-subscription-key

# Azure Storage
AZ_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
AZ_STORAGE_CONTAINER=your-container-name

# Video Input
VIDEO_PATH=path/to/your/video.mp4
```

### Notion Integration (vi_json_to_notion.py)
```env
# Required
NOTION_TOKEN=secret_your_notion_integration_token
NOTION_DATABASE_ID=your-database-id-uuid

# Optional (have defaults)
NOTION_VERSION=2025-09-03
NOTION_TITLE_PROPERTY=Name
NOTION_PAGE_ID=optional-page-id-uuid
NOTION_LOG_PAGE_TITLE=Video Indexer Log
NOTION_STATE_FILE=.notion_state.json
```

## 🔒 Updated .gitignore Recommendations

### Critical Additions:

```gitignore
# Environment & Secrets
.env
.env.local
.env.*.local

# Generated JSON files (contain account IDs, video IDs, metadata)
*.json
!package.json  # If you add one later
!tsconfig.json  # If you add TypeScript later

# Video files (large, shouldn't be in repo)
*.mp4
*.avi
*.mov
*.mkv
*.webm

# Python cache & bytecode
__pycache__/
*.py[cod]
*$py.class
*.so
.Python

# Virtual environment
.venv/
venv/
ENV/
env/

# State files (contain page IDs, ingested video IDs)
.notion_state.json
*.state.json

# IDE & Editor files
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store

# Requirements file (note: you have typo "requirement,txt" - should be "requirements.txt")
requirement*.txt
requirements.txt
requirements-dev.txt

# Logs
*.log
logs/

# Temporary files
*.tmp
*.temp
.cache/
```

## ⚠️ Files Currently in Repo That Should Be Gitignored

1. **`.notion_state.json`** - Contains Notion page ID (`"page_id": "2e0b30c7e85a81419a59e2156ed6136c"`)
   - **Action:** Add `.notion_state.json` explicitly to `.gitignore`

2. **`insights_*.json`** - Already covered by `*.json` pattern ✅

3. **`*.mp4` files** - Already covered ✅

4. **`.venv/` directory** - Should be ignored (virtual environment)
   - **Action:** Add `.venv/` to `.gitignore`

5. **`requirement,txt`** - Typo in filename (comma instead of period)
   - **Action:** Fix filename to `requirements.txt` OR add `requirement*.txt` pattern

## 📋 Complete Recommended .gitignore

```gitignore
# ============================================
# Secrets & Environment
# ============================================
.env
.env.local
.env.*.local

# ============================================
# Generated Output Files
# ============================================
# Video Indexer JSON outputs (contain account IDs, video metadata)
*.json
!package.json
!tsconfig.json

# Video files (large files)
*.mp4
*.avi
*.mov
*.mkv
*.webm

# State files (contain IDs)
.notion_state.json
*.state.json

# ============================================
# Python
# ============================================
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class
*.so

# Virtual environments
.venv/
venv/
ENV/
env/
env.bak/
venv.bak/

# ============================================
# Dependencies
# ============================================
requirements*.txt
requirement*.txt
pip-log.txt
pip-delete-this-directory.txt

# ============================================
# IDE & Editors
# ============================================
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store

# ============================================
# Logs & Temporary
# ============================================
*.log
logs/
*.tmp
*.temp
.cache/
```

## 🎯 Quick Fix Actions

1. **Update `.gitignore`** with the complete version above
2. **Remove tracked files** that shouldn't be in git:
   ```bash
   git rm --cached .notion_state.json
   git rm --cached *.mp4
   git rm --cached insights_*.json
   git rm --cached .venv/ -r  # if it was tracked
   ```
3. **Create `.env` file** with all variables listed above
4. **Fix typo:** Rename `requirement,txt` → `requirements.txt` (or add pattern to gitignore)

## 🔍 What's Safe to Commit

✅ **Safe to commit:**
- `vi_export_json.py`
- `vi_json_to_notion.py`
- `requirements.txt` (once fixed)
- `.gitignore`
- `README.md` (if you have one)

❌ **Never commit:**
- `.env` files
- Generated `insights_*.json` files
- Video files (`.mp4`, etc.)
- `.notion_state.json` (contains page IDs)
- `.venv/` directory
- Any file with API keys, tokens, or connection strings

## 📊 File Size Impact

**Current large files that should be gitignored:**
- `insights_bhu0o3tv9s.json` - ~2409 lines (likely 100-500 KB)
- `insights_obder2hp6x.json` - ~4523 lines (likely 200-800 KB)
- `*.mp4` files - Video files (likely MBs each)

**Total potential savings:** Several MBs by excluding these files from git history.

