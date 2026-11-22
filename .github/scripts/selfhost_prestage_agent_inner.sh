#!/bin/sh
# Runs inside the task image build, as root, on self-hosted runners only.
# Leaves the image in the state Harbor's own claude-code install() would
# leave it in at trial start:
#   - curl, bash, nodejs, npm and procps present (only the missing ones are
#     installed, with apt-get install -y as Harbor does);
#   - apt lists populated from the image's own sources (Harbor runs
#     apt-get update before installing);
#   - the pinned binary at ~/.local/share/claude/versions/<v>, symlinked from
#     ~/.local/bin/claude, owned by the agent user, and the PATH line Harbor
#     appends to ~/.bashrc.
# Harbor then finds `claude --version` == the pinned version and skips its
# install. Any failure here exits 0 without the binary, so Harbor installs as
# usual.
#
# Args: <agent user> <version> [apt mirror base, e.g. http://mirrors.example]
# The binary is bind-mounted at /tmp/ziwu-agent/claude.
set -u
U="$1"; V="$2"; M="${3:-}"
SRC=/tmp/ziwu-agent/claude

say() { echo "prestage: $*"; }

command -v apt-get >/dev/null 2>&1 || { say "no apt-get; leaving install to Harbor"; exit 0; }
if ldd /bin/ls 2>&1 | grep -q musl; then say "musl libc; leaving install to Harbor"; exit 0; fi
H=$(getent passwd "$U" | cut -d: -f6)
[ -n "$H" ] || { say "user $U not found; leaving install to Harbor"; exit 0; }
G=$(id -gn "$U")

need=""
for pair in curl:curl bash:bash node:nodejs npm:npm pgrep:procps; do
  cmd=${pair%%:*}; pkg=${pair#*:}
  command -v "$cmd" >/dev/null 2>&1 || need="$need $pkg"
done

if [ -n "$need" ]; then
  export DEBIAN_FRONTEND=noninteractive
  done_install=0
  # A nearby mirror only changes where the same packages are fetched from.
  if [ -n "$M" ] && [ -r /etc/os-release ]; then
    . /etc/os-release
    CN="${VERSION_CODENAME:-}"
    L=/tmp/ziwu-mirror.list
    [ -n "$CN" ] || ID=unknown
    case "${ID:-}" in
      debian)
        printf 'deb %s/debian %s main\ndeb %s/debian %s-updates main\ndeb %s/debian-security %s-security main\n' \
          "$M" "$CN" "$M" "$CN" "$M" "$CN" > "$L" ;;
      ubuntu)
        C="main restricted universe multiverse"
        printf 'deb %s/ubuntu %s %s\ndeb %s/ubuntu %s-updates %s\ndeb %s/ubuntu %s-security %s\n' \
          "$M" "$CN" "$C" "$M" "$CN" "$C" "$M" "$CN" "$C" > "$L" ;;
      *) : > "$L" ;;
    esac
    if [ -s "$L" ]; then
      O="-o Dir::Etc::SourceList=$L -o Dir::Etc::SourceParts=/nonexistent"
      # shellcheck disable=SC2086
      if apt-get $O update && apt-get $O install -y $need; then done_install=1; fi
      rm -rf /var/lib/apt/lists/*
    fi
    rm -f "$L"
  fi
  if [ "$done_install" = 1 ]; then
    apt-get update || true
  else
    # shellcheck disable=SC2086
    apt-get update && apt-get install -y $need || { say "apt install failed; leaving install to Harbor"; exit 0; }
  fi
fi

[ -x "$SRC" ] || { say "no cached binary; leaving install to Harbor"; exit 0; }
install -d -o "$U" -g "$G" "$H/.local" "$H/.local/share" "$H/.local/share/claude" \
  "$H/.local/share/claude/versions" "$H/.local/bin"
install -m 0755 -o "$U" -g "$G" "$SRC" "$H/.local/share/claude/versions/$V"
ln -sfn "$H/.local/share/claude/versions/$V" "$H/.local/bin/claude"
chown -h "$U:$G" "$H/.local/bin/claude"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$H/.bashrc"
chown "$U:$G" "$H/.bashrc"
say "claude-code $V staged for $U"
