#!/usr/bin/env bash
# Run-store helpers for GitHub Actions (the run store is the `run-store` branch, append-only).
#   scripts/run_store.sh checkout <dir>          check the branch out into <dir> (git worktree)
#   scripts/run_store.sh commit <dir> <message>  commit everything in <dir> and push (rebase + retry if needed)
set -euo pipefail
cmd=$1; dir=$2
case "$cmd" in
  checkout)
    git fetch --quiet origin run-store
    git worktree add --quiet --detach "$dir" FETCH_HEAD
    ;;
  commit)
    msg=$3
    cd "$dir"
    git config user.name "pk-analysis-bot"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add -A
    if git diff --cached --quiet; then echo "run store: nothing to commit"; exit 0; fi
    git commit --quiet -m "$msg"
    for attempt in 1 2 3; do
      if git push --quiet origin HEAD:run-store; then echo "run store: pushed"; exit 0; fi
      git fetch --quiet origin run-store && git rebase --quiet FETCH_HEAD
    done
    echo "run store: push failed" >&2; exit 1
    ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
