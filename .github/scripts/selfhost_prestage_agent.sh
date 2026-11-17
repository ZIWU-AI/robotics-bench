#!/usr/bin/env bash
# runner=self only. Harbor installs the agent into the task container at trial
# start; for claude-code that is ~690 MB per trial (apt nodejs/npm + two
# Claude Code binaries). On a self-hosted host with a narrow uplink, a few
# trials downloading at once overrun Harbor's agent-setup limit.
#
# This appends one layer to the task Dockerfile that leaves the image exactly
# as Harbor's install() would (see selfhost_prestage_agent_inner.sh), so
# Harbor's version check passes and it skips the install. The pinned binary is
# fetched once per host and kept as a local image,
# ziwu-agent-cache/claude-code:<version>; Docker's layer cache then reuses the
# staged layer for every later trial of the same task.
#
# Never fails the job: anything unexpected leaves the Dockerfile untouched and
# Harbor installs the agent as it does on GitHub-hosted runners.
#
# Usage: selfhost_prestage_agent.sh <task path> <agent> <version>
# Env:   ZIWU_APT_MIRROR  optional apt mirror base set on the runner host.
set -uo pipefail
TASK_PATH="$1"; AGENT="$2"; VERSION="${3:-}"
HERE=$(cd "$(dirname "$0")" && pwd)
DF="$TASK_PATH/environment/Dockerfile"

skip() { echo "prestage skipped: $*"; exit 0; }

[ "$AGENT" = "claude-code" ] || skip "no pre-staged install for agent '$AGENT'"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || skip "agent version is not pinned"
[ -f "$DF" ] || skip "no $DF"
grep -q 'selfhost_prestage_agent' "$DF" && skip "already staged"

# The final stage's USER, so it can be restored after the layer.
LAST_USER=$(awk 'toupper($1)=="FROM"{u=""} toupper($1)=="USER"{u=$2} END{print u}' "$DF")
# Harbor sets up the agent as task.toml [agent].user, else the image's user.
AGENT_USER=$(python3 - "$TASK_PATH/task.toml" <<'PY'
import sys, tomllib
try:
    print((tomllib.load(open(sys.argv[1], "rb")).get("agent") or {}).get("user") or "")
except Exception:
    print("")
PY
)
AGENT_USER="${AGENT_USER:-${LAST_USER:-root}}"
[[ "$AGENT_USER" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] || skip "unusual agent user '$AGENT_USER'"
[[ "$LAST_USER" == *'$'* ]] && skip "USER uses a build variable"

CACHE="ziwu-agent-cache/claude-code:$VERSION"
if ! docker image inspect "$CACHE" >/dev/null 2>&1; then
  echo "Fetching claude-code $VERSION into $CACHE (once per host)"
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  B="https://downloads.claude.ai/claude-code-releases/$VERSION"
  sum() { curl -fsSL --retry 3 "$B/$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["platforms"]["linux-x64"]["checksum"])'; }
  RAW_SUM=$(sum manifest.json) || skip "manifest unavailable"
  ZST_SUM=$(sum manifest.zst.json) || skip "zst manifest unavailable"
  curl -fsSL --retry 3 -o "$T/claude.zst" "$B/linux-x64/claude.zst" || skip "download failed"
  echo "$ZST_SUM  $T/claude.zst" | sha256sum -c --quiet || skip "zst checksum mismatch"
  zstd -dq "$T/claude.zst" -o "$T/claude" && rm -f "$T/claude.zst" || skip "zstd failed"
  echo "$RAW_SUM  $T/claude" | sha256sum -c --quiet || skip "binary checksum mismatch"
  chmod 0755 "$T/claude"
  printf 'FROM scratch\nCOPY claude /claude\n' > "$T/Dockerfile"
  docker build -q -t "$CACHE" "$T" >/dev/null || skip "cache image build failed"
fi

INNER_B64=$(base64 -w0 "$HERE/selfhost_prestage_agent_inner.sh")
MIRROR="${ZIWU_APT_MIRROR:-}"
[[ -z "$MIRROR" || "$MIRROR" =~ ^https?://[A-Za-z0-9.:/_-]+$ ]] || MIRROR=""
{
  echo ""
  echo "# --- appended by .github/scripts/selfhost_prestage_agent.sh (runner=self only) ---"
  echo "USER root"
  echo "RUN --mount=type=bind,from=$CACHE,target=/tmp/ziwu-agent echo '$INNER_B64' | base64 -d | sh -s -- '$AGENT_USER' '$VERSION' '$MIRROR' || true"
  [ -n "$LAST_USER" ] && echo "USER $LAST_USER"
} >> "$DF"
echo "Staged claude-code $VERSION for user '$AGENT_USER' in $DF (mirror: ${MIRROR:-image default})"
