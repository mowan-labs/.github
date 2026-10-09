"""Gate decision function for the tiered AI gate (spec.md section 4). Stdlib only.

`decide(...)` is a pure function; the CLI reads a JSON blob of facts the gate
job gathered from the GitHub API and prints the decision. No I/O in decide().

Decision order (first that applies wins):
  1. effective_tier = medium if (tier == low and counts.high > 0) else tier
  2. tests_ok is None -> wait "tests not recorded".
     tests_ok False -> review_ok treated False with reason "tests failing".
  3. review_ok: low -> True; medium/high -> counts.high == 0 and counts.medium == 0.
     fixable_ok = tests_ok and review_ok and not human_changes_requested
                  and unresolved_human_threads == 0
  4. not fixable_ok: fix_rounds >= cap -> needs-human "fix-round cap reached";
     else block (reason lists each failing item).
  5. effective_tier high and not human_approved -> wait "needs approval ...".
  6. touches_github -> needs-human "PR changes .github/; merge by hand".
  7. not automerge_enabled -> shadow "would merge (AI_AUTOMERGE=false)".
  8. else merge.
"""
import json
import sys


def decide(tier, tests_ok, counts, human_approved, human_changes_requested,
           unresolved_human_threads, fix_rounds, fix_round_cap,
           automerge_enabled, touches_github, approvers=None):
    high = int(counts.get("high", 0))
    medium = int(counts.get("medium", 0))
    approvers = approvers or []

    # 1. Effective tier.
    effective_tier = "medium" if (tier == "low" and high > 0) else tier
    reasons = []
    if effective_tier != tier:
        reasons.append("escalated low->medium: review found %d high finding(s)" % high)

    def result(decision, extra_reasons):
        return {"decision": decision,
                "effective_tier": effective_tier,
                "reasons": reasons + extra_reasons}

    # 2. Tests unknown blocks early.
    if tests_ok is None:
        return result("wait", ["tests not recorded"])

    # 3. review_ok / fixable_ok.
    if effective_tier == "low":
        review_ok = True
    else:
        review_ok = (high == 0 and medium == 0)

    fail_items = []
    if not tests_ok:
        fail_items.append("tests failing")
    if not review_ok:
        fail_items.append("review found %d high / %d medium finding(s)" % (high, medium))
    if human_changes_requested:
        fail_items.append("a human requested changes")
    if unresolved_human_threads > 0:
        fail_items.append("%d unresolved human review thread(s)" % unresolved_human_threads)

    fixable_ok = (tests_ok and review_ok and not human_changes_requested
                  and unresolved_human_threads == 0)

    # 4. Not fixable.
    if not fixable_ok:
        if fix_rounds >= fix_round_cap:
            return result("needs-human", ["fix-round cap reached (%d/%d)" % (fix_rounds, fix_round_cap)] + fail_items)
        return result("block", fail_items)

    # 5. High needs approval.
    if effective_tier == "high" and not human_approved:
        who = ", ".join(approvers) if approvers else "an approver"
        return result("wait", ["needs approval from %s" % who])

    # 6. .github/ changes merged by hand.
    if touches_github:
        return result("needs-human", ["PR changes .github/; merge by hand"])

    # 7. Shadow mode.
    if not automerge_enabled:
        return result("shadow", ["would merge (AI_AUTOMERGE=false)"])

    # 8. Merge.
    return result("merge", ["tests + review pass; auto-merging"])


def _to_tests_ok(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("", "none", "null", "unknown"):
        return None
    if s in ("true", "success", "passed", "pass", "ok", "1"):
        return True
    return False


def main(argv):
    # Reads a JSON facts object from stdin or argv[1] (a file path), prints
    # {"decision","effective_tier","reasons"}.
    if len(argv) > 1:
        with open(argv[1]) as fh:
            facts = json.load(fh)
    else:
        facts = json.load(sys.stdin)

    result = decide(
        tier=facts["tier"],
        tests_ok=_to_tests_ok(facts.get("tests_ok")),
        counts=facts.get("counts") or {},
        human_approved=bool(facts.get("human_approved")),
        human_changes_requested=bool(facts.get("human_changes_requested")),
        unresolved_human_threads=int(facts.get("unresolved_human_threads") or 0),
        fix_rounds=int(facts.get("fix_rounds") or 0),
        fix_round_cap=int(facts.get("fix_round_cap") or 3),
        automerge_enabled=bool(facts.get("automerge_enabled")),
        touches_github=bool(facts.get("touches_github")),
        approvers=facts.get("approvers") or [],
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
