# mowan-labs/.github

Shared CI for mowan-labs repos: an LLM reviews every PR, and the PR merges
itself when both the repo's tests and the review pass.

| Path | What it is |
|---|---|
| `.github/workflows/reusable-ai-review.yml` | Reusable workflow: tests, review, gated merge |
| `.github/workflows/ai-review.yml` | This repo's own caller (review only, manual merge) |
| `actions/ai-review/` | Composite action + Python reviewer on Bedrock (boto3, pinned) |
| `templates/caller-ai-review.yml` | The ~20-line file each repo carries |
| `scripts/onboard-repo.sh` | Adds a repo: secret, variable, permission, caller PR |

## How a PR flows

1. `tests` runs the repo's `test-command`.
2. `review` sends `base...head` to the model and posts an approve or
   request-changes review. A high-severity finding fails the job.
3. `merge` runs only if both jobs pass, and only at the reviewed head commit
   (`--match-head-commit`), so a later unreviewed push cannot ride along.

The reviewer fails closed: an API error, a malformed reply, a missing model or
a diff over 150k chars all produce request-changes.

## Bedrock credentials

Two modes; the reviewer fails closed if neither is configured.

**OIDC role (preferred, no credential stored anywhere).** Repo **secret**
`AWS_ROLE_ARN` plus repo variables `AWS_REGION` and `REVIEW_MODEL`. The ARN is
not a credential -- only a GitHub-issued OIDC token from a repo the trust
policy names can assume the role, and GitHub never issues one to fork PRs --
but it is a secret so the account ID is masked in this public repo's run logs.
In your AWS account, once:

1. IAM -> Identity providers -> add OpenID Connect provider
   `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.
2. Create a role with this trust policy:

```json
{"Version": "2012-10-17", "Statement": [{
  "Effect": "Allow",
  "Principal": {"Federated": "arn:aws:iam::<ACCOUNT>:oidc-provider/token.actions.githubusercontent.com"},
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
    "StringLike":   {"token.actions.githubusercontent.com:sub": [
      "repo:mowan-labs/*:pull_request", "repo:xysr89/*:pull_request"]}}}]}
```

3. Grant it only `bedrock:InvokeModel`:

```json
{"Version": "2012-10-17", "Statement": [{
  "Effect": "Allow", "Action": "bedrock:InvokeModel",
  "Resource": [
    "arn:aws:bedrock:*::foundation-model/anthropic.*",
    "arn:aws:bedrock:*:<ACCOUNT>:inference-profile/*"]}]}
```

4. Enable the model in the Bedrock console (Model access) for that region.

**Bedrock API key.** Repo secret `AWS_BEARER_TOKEN_BEDROCK` plus variables
`AWS_REGION` and `REVIEW_MODEL`. Simpler, but long-lived: set an expiry and
rotate it.

## Onboard a repo

```bash
AWS_ROLE_ARN=arn:aws:iam::<ACCOUNT>:role/<role> AWS_REGION=us-east-1 \
REVIEW_MODEL=<bedrock-model-or-profile-id> \
  scripts/onboard-repo.sh <repo-name> '<test command>'
```

## Safety valves

- **PRs that touch `.github/` never auto-merge.** A `pull_request` run uses the
  PR's own caller workflow, so such a PR could have weakened its own gate. The
  merge job comments and stops; a human merges.
- **Kill switch:** set the variable `AI_AUTOMERGE=false` on a repo (or the org)
  to stop all auto-merges at once. Reviews keep running.

## Known limits

- **Free plan:** no branch protection on private repos, so nothing stops a
  manual merge of a red PR. The gate is the workflow's own merge job.
- **Merges use `GITHUB_TOKEN`**, which does not trigger other workflows (for
  example a deploy on push to `main`). Switch the merge job to a GitHub App
  token when that matters.
- **Callers track `@main`.** Tag a release (`v1`) and pin callers to it once
  the workflow stabilises.
