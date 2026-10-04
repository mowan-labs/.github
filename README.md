# mowan-labs/.github

Shared CI for mowan-labs repos: an LLM reviews every PR, and the PR merges
itself when both the repo's tests and the review pass.

| Path | What it is |
|---|---|
| `.github/workflows/ai-review.yml` | Reusable workflow: tests, review, gated merge |
| `actions/ai-review/` | Composite action + stdlib Python reviewer |
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

## Onboard a repo

```bash
ANTHROPIC_API_KEY=... REVIEW_MODEL=<model-id> \
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
