#!/usr/bin/env bash
#
# Pick a local branch, fast-forward the current branch to it, then remove the
# branch's worktree (if it has one) and delete the branch.

set -euo pipefail

current="$(git branch --show-current)"
if [ -z "$current" ]; then
    echo "Error: HEAD is detached; check out the branch to merge into first." >&2
    exit 1
fi

branches=()
while read -r b; do
    if [ "$b" != "$current" ]; then
        branches+=("$b")
    fi
done < <(git branch --format='%(refname:short)')

if [ ${#branches[@]} -eq 0 ]; then
    echo "No other local branches." >&2
    exit 1
fi

branch=""
PS3="Branch to fast-forward $current to: "
select branch in "${branches[@]}"; do
    [ -n "$branch" ] && break
done
[ -n "$branch" ] || exit 1

worktree="$(git branch --list "$branch" --format='%(worktreepath)')"

git merge --ff-only "$branch"
if [ -n "$worktree" ]; then
    git worktree remove "$worktree"
fi
git branch -d "$branch"
