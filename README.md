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

## Tiered gate + AI coder

A second, opt-in layer sits beside the legacy reviewer. It classifies every PR
into **low / medium / high** risk and runs an AI coder that can open and self-fix
PRs. The legacy `reusable-ai-review.yml` and `actions/ai-review` legacy mode are
untouched; existing callers keep working.

| Path | What it is |
|---|---|
| `.github/workflows/reusable-ai-gate.yml` | Reusable tiered gate: classify, test, review, decide |
| `.github/workflows/reusable-ai-coder.yml` | Reusable AI coder: `implement` an issue / `fix` a PR |
| `actions/risk-classify/` | `classify.py` + action: tier from the base-commit policy |
| `actions/ai-gate/` | `gate.py` + action: pure decision + act (merge/dispatch/mention) |
| `templates/caller-ai-gate.yml` | `.github/workflows/ai-gate.yml` each repo carries |
| `templates/caller-ai-coder.yml` | `.github/workflows/ai-coder.yml` each repo carries |

### Risk policy

Each adopting repo adds `.github/risk-policy.json`, read from the PR's **base**
commit (not head, so a PR cannot relax its own policy):

```json
{
  "version": 1,
  "high":   {"paths": ["infra/**"], "deleted_paths": ["**/tests/**"]},
  "medium": {"paths": ["pipeline/**"], "max_changed_lines": 300}
}
```

Any change under `.github/` is **high** regardless of policy (hard floor). A
missing or invalid policy is treated as **medium**.

Only changes that can alter system behaviour count. Documentation is dropped
from the changed paths, the deleted paths and the line budget before the
`high`/`medium` rules run, so a docs-only PR is **low** and a README inside
`infra/` does not make a PR high. The defaults, which a policy can override with
an optional `non_behavioral` block:

```json
"non_behavioral": {
  "paths":  ["docs/**", "**/*.md", "**/*.rst", "**/*.adoc",
             "**/LICENSE", "**/LICENSE.*", "**/NOTICE", "**/NOTICE.*"],
  "except": [".kiro/**", "**/prompts/**", "**/SKILL.md", "**/AGENTS.md", "**/CLAUDE.md"]
}
```

`except` keeps agent-instruction markdown in scope, because prompts change what
an agent does.

### How a PR flows per tier

1. **classify** writes `mowan/risk` (`tier=<low|medium|high>`).
2. **tests** writes `mowan/tests` (`tests passed` / `tests failed`).
3. **review** (tiered mode) posts one COMMENT review and writes `mowan/ai-review`
   (`high=<n> medium=<n> low=<n>`) — never an APPROVE, because the bot cannot
   approve its own PR.
4. **decide** writes `mowan/gate` (`<decision>: <reason>`) and acts:
   - **low** — merges after CI + an advisory review. A high finding escalates it
     to medium.
   - **medium** — merges only with a clean blocking review (no high/medium
     findings) and CI green.
   - **high** — also needs Mingchen's **approving** review at the reviewed head
     commit.
   - Any blocking state on an `ai/*` branch dispatches the coder to self-fix,
     up to `fix-round-cap` (default 3), then escalates to `needs-human`.
   - A PR that touches `.github/` is always `needs-human` (merge by hand).

The statuses are the state of record, keyed by head SHA; a review-event run
reuses recorded CI and review results instead of re-billing the model.

### Statuses

`mowan/risk`, `mowan/tests`, `mowan/ai-review`, `mowan/gate` — all keyed by the
head SHA. The gate keeps one sticky PR comment (`<!-- mowan-gate -->`) showing
tier, decision, reasons and fix round.

### Kill switches

- **`AI_AUTOMERGE`** — set the repo (or org) variable to `false` and the gate
  runs fully but only reports **shadow** ("would merge"); nothing is merged.
- **`AI_CODER`** — set to `false` to disable the coder (it exits 0 with a notice).

### Shadow-mode onboarding

Adopting repos set `AI_AUTOMERGE=false` at onboarding, so the gate observes and
comments without merging until you trust it. Flip the variable to enable merges.

### Known limit (free plan)

As with the legacy reviewer, private repos on the free plan have no branch
protection: **nothing stops a human from merging a red or un-gated PR by hand.**
The gate's own merge step is the only automated gate.
