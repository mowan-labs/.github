# mowan-labs/.github

Shared CI for mowan-labs repos: an LLM reviews every PR, and the PR merges
itself when both the repo's tests and the review pass.

| Path | What it is |
|---|---|
| `.github/workflows/reusable-ai-review.yml` | Reusable workflow: tests, review, gated merge |
| `.github/workflows/ai-review.yml` | This repo's own caller (review only, manual merge) |
| `actions/ai-review/` | Composite action + Python wrapper around Kiro CLI headless mode |
| `tests/` | Unit tests for the wrapper (`python3 -m unittest discover -s tests`) |
| `templates/caller-ai-review.yml` | The ~20-line file each repo carries |
| `scripts/onboard-repo.sh` | Adds a repo: secret, variable, permission, caller PR |

## How a PR flows

1. `tests` runs the repo's `test-command`.
2. `review` sends `base...head` to the model and posts an approve or
   request-changes review. A high-severity finding fails the job.
3. `merge` runs only if both jobs pass, and only at the reviewed head commit
   (`--match-head-commit`), so a later unreviewed push cannot ride along.

The reviewer fails closed: a missing key, a Kiro CLI error, a malformed reply or
a diff over 150k chars all produce request-changes.

## Reviewer: Kiro CLI headless mode

The review step installs [Kiro CLI](https://kiro.dev/docs/cli/headless/) and
runs `kiro-cli chat --no-interactive` with the diff on stdin. It grants **no
tools**, so a prompt injected through the diff cannot read files or the
environment.

| Setting | Type | Required |
|---|---|---|
| `KIRO_API_KEY` | Repo secret (`ksk_...`, from [app.kiro.dev](https://app.kiro.dev)) | Yes |
| `REVIEW_MODEL` | Repo variable, a Kiro model ID | No -- Kiro's default model when unset |

Headless API keys need a Kiro Pro, Pro+, Pro Max or Power subscription. The key
is long-lived and tied to your Kiro account and its usage: keep it in repo
secrets only, rotate it, and revoke it from the Kiro portal if it leaks.
GitHub never passes secrets to fork PRs, so outside contributors cannot use it.

## Onboard a repo

```bash
KIRO_API_KEY=ksk_... scripts/onboard-repo.sh <repo-name|owner/repo> '<test command>'
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
  example a deploy on push to `mainline`). Switch the merge job to a GitHub App
  token when that matters.
- **Callers track `@mainline`.** Tag a release (`v1`) and pin callers to it once
  the workflow stabilises.
