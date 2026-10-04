#!/usr/bin/env bash
# Onboard one mowan-labs repo to the shared AI review + auto-merge workflow.
#
# Usage:
#   KIRO_API_KEY=ksk_... [REVIEW_MODEL=<kiro model id>] \
#     scripts/onboard-repo.sh <repo-name|owner/repo> '<test command>'
#
# Example:
#   scripts/onboard-repo.sh mowan-graph 'pip install -e .[dev] && pytest -q'
#
# What it does:
#   1. Sets the KIRO_API_KEY secret (and REVIEW_MODEL variable, if given).
#   2. Lets GitHub Actions approve PRs in that repo.
#   3. Opens a PR adding .github/workflows/ai-review.yml -- that PR is itself
#      the first thing the new reviewer reviews.
set -euo pipefail

ORG=mowan-labs
REPO_NAME=${1:?usage: onboard-repo.sh <repo-name|owner/repo> '<test command>'}
TEST_CMD=${2:?usage: onboard-repo.sh <repo-name|owner/repo> '<test command>'}
# A bare name means mowan-labs; owner/repo works for any owner (.github is public).
case "$REPO_NAME" in */*) REPO="$REPO_NAME" ;; *) REPO="$ORG/$REPO_NAME" ;; esac
: "${KIRO_API_KEY:?set KIRO_API_KEY}"
HERE=$(cd "$(dirname "$0")/.." && pwd)

echo "==> secrets / variables on $REPO"
# Piped via stdin so the key never appears in the process list.
printf '%s' "$KIRO_API_KEY" | gh secret set KIRO_API_KEY --repo "$REPO"
if [ -n "${REVIEW_MODEL:-}" ]; then
  gh variable set REVIEW_MODEL --repo "$REPO" --body "$REVIEW_MODEL"
fi

echo "==> allow Actions to approve PRs"
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=true >/dev/null

echo "==> open onboarding PR"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
gh repo clone "$REPO" "$WORK/repo" -- --quiet
cd "$WORK/repo"
BRANCH=ci/ai-review
git switch -c "$BRANCH"
mkdir -p .github/workflows
# YAML single-quoted scalar: double any single quotes in the command.
QUOTED="'${TEST_CMD//\'/\'\'}'"
python3 - "$HERE/templates/caller-ai-review.yml" "$QUOTED" > .github/workflows/ai-review.yml <<'PY'
import sys
print(open(sys.argv[1]).read().replace("__TEST_COMMAND__", sys.argv[2]), end="")
PY
git add .github/workflows/ai-review.yml
git commit -q -m "ci: add shared AI review and auto-merge"
git push -q -u origin "$BRANCH"
gh pr create --repo "$REPO" --head "$BRANCH" \
  --title "ci: add shared AI review and auto-merge" \
  --body "Adds the caller for mowan-labs/.github's reusable AI review workflow. Tests: \`$TEST_CMD\`."
