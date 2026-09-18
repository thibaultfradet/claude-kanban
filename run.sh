#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt
mkdir -p data/logs

# Don't rely on the launching shell's PATH for the claude binary: a long-lived
# terminal tab may predate a PATH change (e.g. the CLI installer appending
# ~/.local/bin), leaving it silently unresolvable for every ticket run.
export KANBAN_CLAUDE_BIN="${KANBAN_CLAUDE_BIN:-$HOME/.local/bin/claude}"

exec uvicorn app.main:app --host 127.0.0.1 --port 8787
