#!/bin/bash

# Online-control cases (tasks that vendor tools/case-kit, i.e. contain a casekit/
# directory) have exactly ONE time limit: [agent].timeout_sec, which the harness
# starts with the agent and which instruction.md states in its last line
# ("You have N seconds ..."). When it runs out the collector seals the episode.
#
# A second clock kept by the service ("N wall seconds from the first request",
# "info does not start the timer") starts later than the harness clock, so it
# tells the agent it has time it does not have. Agents that read it that way
# spend the real budget on offline preparation and are killed before they ever
# act. The case kit no longer accepts a wall_s budget; this check catches the
# rest early:
#
#   1. no wall_s budget in what the service or verifier loads (environment/, tests/);
#   2. neither instruction.md nor the docs under environment/ describe a service-side wall-clock budget or timer;
#   3. [agent].timeout_sec is at least the template's 18000 s (5h): the same
#      single limit every task gets while authoring, lifted to 28800 s (8h) with
#      the rest of the suite before a release (see check-task-timeout.sh).

set -e

MIN_CASE_TIMEOUT_SEC=18000

if [ $# -eq 0 ]; then
    TASK_DIRS=$(find tasks -mindepth 1 -maxdepth 1 -type d 2>/dev/null)
else
    TASK_DIRS="$@"
fi

if [ -z "$TASK_DIRS" ]; then
    echo "No task directories to check"
    exit 0
fi

FAILED=0
for task_dir in $TASK_DIRS; do
    [ -d "$task_dir" ] || continue
    # Only cases: a vendored casekit/ directory anywhere in the task.
    if [ -z "$(find "$task_dir" -type d -name casekit -print -quit)" ]; then
        continue
    fi
    echo "Checking $task_dir (case)..."

    OUT=$(python3 - "$task_dir" "$MIN_CASE_TIMEOUT_SEC" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

task = Path(sys.argv[1])
min_sec = int(sys.argv[2])
problems = []

# 1. A wall_s budget in what the service or verifier loads (environment/, tests/); the vendored casekit/ copies
#    are the kit's. Only budgets count: a measured "wall_s" elsewhere (authoring notes, metrics) is not a clock.
def budget_hits(obj, inside=False):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if inside and k == "wall_s":
                yield True
            yield from budget_hits(v, inside or "budget" in str(k).lower())
    elif isinstance(obj, list):
        for v in obj:
            yield from budget_hits(v, inside)

for top in ("environment", "tests"):
    for f in sorted((task / top).rglob("*")):
        if not f.is_file() or "casekit" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if f.suffix == ".json":
            try:
                hit = any(budget_hits(json.loads(text)))
            except ValueError:
                hit = False
            if hit:
                problems.append(f"{f}: its budgets set wall_s; the case kit rejects it. "
                                f"The only time limit is [agent].timeout_sec")
        elif f.suffix == ".py":
            for n, line in enumerate(text.splitlines(), 1):
                if re.search(r"budget", line, re.I) and re.search(r"\bwall_s\b", line):
                    problems.append(f"{f}: line {n} puts wall_s in a budget; the case kit rejects it. "
                                    f"The only time limit is [agent].timeout_sec")

# 2. A second clock described to the agent.
# A budget, not a mention: "Physics never advances with wall-clock time" is fine.
CLOCK = re.compile(
    r"\b\d[\d,.]*\s*(?:s|sec|seconds?)?\s*(?:of\s+)?wall[- ]?(?:clock[- ]?)?(?:time|seconds?|s)\b"  # 1800 wall seconds
    r"|wall[- ]?(?:clock[- ]?)?(?:time[- ]?)?(?:budget|timer|limit|deadline)\b"                    # wall-clock budget
    r"|\b(?:start|starts|started|starting)\s+the\s+(?:wall[- ]?)?(?:clock|timer)\b"               # does not start the timer
    r"|\bseconds?\s+(?:of\s+wall\s+time\s+)?from\s+the\s+first\s+(?:request|call|operation)\b",
    re.IGNORECASE)
# instruction.md and the documentation shipped in the image (e.g. /app/API.md from environment/).
docs = [task / "instruction.md"] + sorted(f for f in (task / "environment").rglob("*.md") if "casekit" not in f.parts)
for doc in docs:
    if not doc.is_file():
        continue
    for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        m = CLOCK.search(line)
        if m:
            problems.append(f"{doc}: line {n} describes a service-side clock ('{m.group(0)}'). "
                            f"Remove it; the last line of instruction.md ('You have N seconds ...') is the only "
                            f"time limit")

# 3. The single limit is the template's.
toml = task / "task.toml"
if toml.is_file():
    with open(toml, "rb") as fh:
        value = tomllib.load(fh).get("agent", {}).get("timeout_sec")
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = None
    if value is None or value < min_sec:
        problems.append(f"{toml}: [agent].timeout_sec={value!r} is below {min_sec} s. Cases use the template's "
                        f"single limit ({min_sec} s while authoring, 28800 s after the release lift); "
                        f"set it here and in instruction.md's 'You have N seconds' line")

for p in problems:
    print(p)
sys.exit(1 if problems else 0)
PYEOF
    ) && RC=0 || RC=$?
    if [ "$RC" -ne 0 ]; then
        echo "$OUT" | while IFS= read -r line; do
            [ -n "$line" ] && echo "FAIL ${line}"
        done
        FAILED=1
    fi
done

if [ $FAILED -eq 1 ]; then
    echo ""
    echo "A case has a time limit besides [agent].timeout_sec."
    echo "  Re-sync the kit (python tools/case-kit/sync.py <task>), drop wall_s from the"
    echo "  budgets and from instruction.md, and set [agent].timeout_sec to ${MIN_CASE_TIMEOUT_SEC}"
    echo "  (and the 'You have N seconds' line with it)."
    exit 1
fi

echo "Case time limits OK"
