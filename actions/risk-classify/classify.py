"""Risk classifier for the tiered AI gate (spec.md section 1). Stdlib only.

Reads the risk policy from the PR's BASE commit (never head), inspects the
changed/deleted paths and the numstat line counts, and emits
{"tier": "...", "reasons": [...]}.

Pure functions (classify, match_glob, parse_numstat) are unit-tested; the CLI
is a thin shell that gathers git facts and calls classify().

Classification, first match wins top-down:
  1. any changed path under .github/ -> high (hard floor, not configurable)
  2. policy missing / invalid JSON / wrong schema -> medium "policy missing or invalid"
  3. any changed path matches high.paths, or any deleted path matches
     high.deleted_paths -> high
  4. any changed path matches medium.paths, or added+deleted lines
     (numstat; binary counts 0) > medium.max_changed_lines -> medium
  5. otherwise low
"""
import json
import subprocess
import sys


def match_glob(pattern, path):
    """Match a repo-relative POSIX path against a glob.

    `**` spans directories (including zero segments); `*` and `?` stay inside a
    single path segment. Matching is anchored at both ends.
    """
    return _match(pattern.split("/"), path.split("/"))


def _match(pat, parts):
    # pat, parts: lists of path segments.
    if not pat:
        return not parts
    head = pat[0]
    if head == "**":
        rest = pat[1:]
        if not rest:
            return True  # trailing ** matches everything remaining (incl. zero)
        # ** matches zero or more leading segments; try every split.
        for i in range(len(parts) + 1):
            if _match(rest, parts[i:]):
                return True
        return False
    if not parts:
        return False
    if _seg_match(head, parts[0]):
        return _match(pat[1:], parts[1:])
    return False


def _seg_match(pat, seg):
    """Match a single segment: `*` any run (not /), `?` one char (not /)."""
    return _seg(pat, seg)


def _seg(pat, seg):
    # Classic recursive wildcard match within one segment.
    if not pat:
        return not seg
    if pat[0] == "*":
        # match zero or more chars within the segment
        return _seg(pat[1:], seg) or (bool(seg) and _seg(pat, seg[1:]))
    if not seg:
        return False
    if pat[0] == "?" or pat[0] == seg[0]:
        return _seg(pat[1:], seg[1:])
    return False


def _valid_policy(policy):
    """Shape check: top-level dict; high/medium (if present) are dicts whose
    known keys hold the right types. Unknown keys are ignored."""
    if not isinstance(policy, dict):
        return False
    for tier in ("high", "medium"):
        if tier in policy and not isinstance(policy[tier], dict):
            return False
    high = policy.get("high") or {}
    med = policy.get("medium") or {}
    for key in ("paths", "deleted_paths"):
        if key in high and not _is_str_list(high[key]):
            return False
    if "paths" in med and not _is_str_list(med["paths"]):
        return False
    if "max_changed_lines" in med and not isinstance(med["max_changed_lines"], int):
        return False
    return True


def _is_str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def classify(policy_text, changed_paths, deleted_paths, changed_lines):
    """Pure classifier. policy_text is the raw base-commit policy file content
    (or None if absent). Returns {"tier", "reasons"}.

    changed_paths: every added/modified/renamed path (new names included).
    deleted_paths: paths removed (and old names of renames).
    changed_lines: added+deleted lines across the diff (numstat sum, binary 0).
    """
    reasons = []

    # Rule 1: hard floor for .github/ (not configurable).
    gh = [p for p in changed_paths if p == ".github" or p.startswith(".github/")]
    if gh:
        return {"tier": "high",
                "reasons": ["changed path under .github/: %s" % gh[0]]}

    # Rule 2: policy missing / invalid.
    policy = None
    if policy_text is not None:
        try:
            policy = json.loads(policy_text)
        except ValueError:
            policy = None
    if policy is None or not _valid_policy(policy):
        return {"tier": "medium", "reasons": ["policy missing or invalid"]}

    high = policy.get("high") or {}
    med = policy.get("medium") or {}

    # Rule 3: high paths / high deleted paths.
    hit = False
    for pat in high.get("paths", []):
        for p in changed_paths:
            if match_glob(pat, p):
                reasons.append("high path %s matched by %s" % (p, pat))
                hit = True
    for pat in high.get("deleted_paths", []):
        for p in deleted_paths:
            if match_glob(pat, p):
                reasons.append("high deleted path %s matched by %s" % (p, pat))
                hit = True
    if hit:
        return {"tier": "high", "reasons": reasons}

    # Rule 4: medium paths / line budget.
    hit = False
    for pat in med.get("paths", []):
        for p in changed_paths:
            if match_glob(pat, p):
                reasons.append("medium path %s matched by %s" % (p, pat))
                hit = True
    cap = med.get("max_changed_lines")
    if isinstance(cap, int) and changed_lines > cap:
        reasons.append("changed lines %d > max_changed_lines %d" % (changed_lines, cap))
        hit = True
    if hit:
        return {"tier": "medium", "reasons": reasons}

    # Rule 5.
    return {"tier": "low", "reasons": ["no high/medium rule matched"]}


def parse_name_status(text):
    """Parse `git diff --name-status -M <base>...<head>` output into
    (changed_paths, deleted_paths). Renames (Rxxx) count both old and new:
    new path is changed, old path is deleted. Copies (Cxxx) count the new path."""
    changed, deleted = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0]
        if code.startswith("R") and len(parts) >= 3:
            old, new = parts[1], parts[2]
            deleted.append(old)
            changed.append(new)
        elif code.startswith("C") and len(parts) >= 3:
            changed.append(parts[2])
        elif code.startswith("D"):
            deleted.append(parts[1])
        elif len(parts) >= 2:  # A, M, T, etc.
            changed.append(parts[1])
    return changed, deleted


def parse_numstat(text):
    """Sum added+deleted lines from `git diff --numstat`. Binary files show
    `-\t-` and count 0."""
    total = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        a, d = cols[0], cols[1]
        if a == "-" or d == "-":
            continue
        try:
            total += int(a) + int(d)
        except ValueError:
            continue
    return total


def _git(args):
    return subprocess.run(["git"] + args, capture_output=True, text=True, check=True).stdout


def _read_base_policy(base_sha, policy_path):
    try:
        return _git(["show", "%s:%s" % (base_sha, policy_path)])
    except subprocess.CalledProcessError:
        return None  # file absent in base -> missing policy


def main(argv):
    # Usage: classify.py <base_sha> <head_sha> <policy_path> <out.json>
    base_sha, head_sha, policy_path, out_path = argv[1], argv[2], argv[3], argv[4]
    rng = "%s...%s" % (base_sha, head_sha)
    name_status = _git(["diff", "--name-status", "-M", rng])
    numstat = _git(["diff", "--numstat", rng])
    changed, deleted = parse_name_status(name_status)
    lines = parse_numstat(numstat)
    policy_text = _read_base_policy(base_sha, policy_path)
    result = classify(policy_text, changed, deleted, lines)
    with open(out_path, "w") as fh:
        json.dump(result, fh)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
