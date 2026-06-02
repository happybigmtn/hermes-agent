#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
source_dir="$repo_root/scripts/dev-shortcuts"
bin_dir="${DEV_SHORTCUT_BIN_DIR:-$HOME/.local/bin}"
global_bin_dir="${DEV_SHORTCUT_GLOBAL_BIN_DIR:-/usr/local/bin}"
install_global="${DEV_SHORTCUT_INSTALL_GLOBAL:-auto}"
dry_run=0

usage() {
  cat <<'EOF'
usage: scripts/install-dev-shortcuts.sh [--dry-run] [--no-global]

Installs dev-orchestrator shortcuts:
  ds db dw dnew dstart dsteer dclose dattach dtail dhelp

Defaults:
  local bin:  ~/.local/bin
  global bin: /usr/local/bin when sudo is available

Environment:
  DEV_SHORTCUT_BIN_DIR=/path
  DEV_SHORTCUT_GLOBAL_BIN_DIR=/path
  DEV_SHORTCUT_INSTALL_GLOBAL=auto|1|0
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      ;;
    --no-global)
      install_global=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

run() {
  if [ "$dry_run" = 1 ]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

commands=(ds db dw dnew dstart dsteer dclose dattach dtail dhelp)

run mkdir -p "$bin_dir"
for name in "${commands[@]}"; do
  run install -m 0755 "$source_dir/$name" "$bin_dir/$name"
done
run install -m 0644 "$source_dir/bash_aliases" "$HOME/.bash_aliases"

should_global=0
if [ "$install_global" = 1 ]; then
  should_global=1
elif [ "$install_global" = auto ] && command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  should_global=1
fi

if [ "$should_global" = 1 ]; then
  for name in "${commands[@]}"; do
    run sudo ln -sf "$bin_dir/$name" "$global_bin_dir/$name"
  done
fi

cat <<EOF
Dev shortcuts installed.
local bin:  $bin_dir
global bin: $([ "$should_global" = 1 ] && echo "$global_bin_dir" || echo "skipped")

Try:
  dhelp
  ds
  db
EOF
