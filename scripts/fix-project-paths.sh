#!/usr/bin/env bash
# fix-project-paths.sh — 改写会话里的 cwd，让老会话指向新路径
# 用法：
#   ./fix-project-paths.sh --old=/Users/old --new=/Users/new
#   ./fix-project-paths.sh --old=/Users/old --new=/Users/new --target=$HOME/.claude
# 说明：改写前自动留 .pre-fix.bak；.claude.json 只写 .fixed 副本，确认后手动替换。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OLD=""; NEW=""; TARGET=""; DRY=""

for a in "$@"; do
  case "$a" in
    --old=*) OLD="${a#*=}" ;;
    --new=*) NEW="${a#*=}" ;;
    --target=*) TARGET="${a#*=}" ;;
    --dry-run) DRY="--dry-run" ;;
    -h|--help) echo "用法: $0 --old=<原根> --new=<新根> [--target=<配置目录>] [--dry-run]"; exit 0 ;;
    *) echo "未知参数: $a"; exit 2 ;;
  esac
done

if [ -z "$OLD" ] || [ -z "$NEW" ]; then
  echo "用法: $0 --old=<原根> --new=<新根> [--target=<配置目录>]"
  exit 2
fi

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -z "$PY" ] && { echo "未找到 Python 3"; exit 1; }

if [ -z "$TARGET" ]; then
  TARGET="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
fi

exec "$PY" "$HERE/restore.py" fix-paths --old "$OLD" --new "$NEW" --target "$TARGET" $DRY
