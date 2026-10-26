#!/bin/bash
# Blocks "done" until black passes, versions are consistent, and the version is bumped on feature branches.
cd "$CLAUDE_PROJECT_DIR" || exit 0
[ "$(jq -r '.stop_hook_active' 2>/dev/null)" = "true" ] && exit 0
err=""
files=$( (git diff --name-only HEAD; git ls-files --others --exclude-standard) | grep '\.py$' | sort -u | xargs -I{} sh -c 'test -f "{}" && echo "{}"')
if [ -n "$files" ]; then
  out=$(echo "$files" | xargs poetry run black --check 2>&1) || err+="black check failed:\n$out\n"
fi
pv=$(grep -oE '^version = "[^"]+"' pyproject.toml | cut -d'"' -f2)
dv=$(grep -oE '^release = "[^"]+"' docs/conf.py | cut -d'"' -f2)
[ "$pv" != "$dv" ] && err+="pyproject.toml version ($pv) != docs/conf.py release ($dv).\n"
branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$branch" != "main" ] && git diff --name-only main...HEAD HEAD 2>/dev/null | grep -q '^eflips/'; then
  mv=$(git show main:pyproject.toml | grep -oE '^version = "[^"]+"' | cut -d'"' -f2)
  [ "$mv" = "$pv" ] && err+="Version not bumped vs main ($pv). Bump per SemVer in pyproject.toml and docs/conf.py.\n"
fi
if [ -n "$err" ]; then
  jq -n --arg r "$(printf "$err")" '{decision:"block", reason:$r}'
fi
exit 0
