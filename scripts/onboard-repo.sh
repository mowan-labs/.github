#!/usr/bin/env bash
# Onboard one mowan-labs repo to the shared AI review + auto-merge workflow.
#
# Usage (OIDC role, preferred -- no stored secret):
#   AWS_ROLE_ARN=arn:aws:iam::<acct>:role/<role> AWS_REGION=us-east-1 \
#   REVIEW_MODEL=<bedrock model or inference-profile id> \
#     scripts/onboard-repo.sh <repo-name> '<test command>'
# Usage (Bedrock API key instead of a role): set BEDROCK_KEY instead of
#   AWS_ROLE_ARN; it is stored as the repo secret AWS_BEARER_TOKEN_BEDROCK.
#
# Example:
#   scripts/onboard-repo.sh mowan-graph 'pip install -e .[dev] && pytest -q'
#
# What it does:
#   1. Sets REVIEW_MODEL / AWS_REGION / AWS_ROLE_ARN variables (or the
#      Bedrock API key secret) on the repo.
#   2. Lets GitHub Actions approve PRs in that repo.
#   3. Opens a PR adding .github/workflows/ai-review.yml -- that PR is itself
#      the first thing the new reviewer reviews.
set -euo pipefail

ORG=mowan-labs
REPO_NAME=${1:?usage: onboard-repo.sh <repo-name> '<test command>'}
TEST_CMD=${2:?usage: onboard-repo.sh <repo-name> '<test command>'}
REPO="$ORG/$REPO_NAME"
: "${REVIEW_MODEL:?set REVIEW_MODEL}"
: "${AWS_REGION:?set AWS_REGION}"
if [ -z "${AWS_ROLE_ARN:-}" ] && [ -z "${BEDROCK_KEY:-}" ]; then
  echo "set AWS_ROLE_ARN (OIDC, preferred) or BEDROCK_KEY" >&2; exit 1
fi
HERE=$(cd "$(dirname "$0")/.." && pwd)

echo "==> secrets / variables on $REPO"
gh variable set REVIEW_MODEL --repo "$REPO" --body "$REVIEW_MODEL"
gh variable set AWS_REGION --repo "$REPO" --body "$AWS_REGION"
if [ -n "${AWS_ROLE_ARN:-}" ]; then
  gh variable set AWS_ROLE_ARN --repo "$REPO" --body "$AWS_ROLE_ARN"
else
  # Piped via stdin so the key never appears in the process list.
  printf '%s' "$BEDROCK_KEY" | gh secret set AWS_BEARER_TOKEN_BEDROCK --repo "$REPO"
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
