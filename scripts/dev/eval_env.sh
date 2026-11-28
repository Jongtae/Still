#!/bin/sh
# #1297: create or update the evaluation virtualenv. Still's runtime dependencies come from
# the project's own lock (uv.lock), resolved together with evals/requirements.txt, so a
# conflict fails the install instead of drifting a locked version.
#
# usage: sh scripts/dev/eval_env.sh [venv]    (PYTHON selects the interpreter, 3.12 or newer)
set -eu
venv=${1:-$HOME/.local/share/agentos-evals/venv}
root=$(cd "$(dirname "$0")/../.." && pwd)
[ -x "$venv/bin/python" ] || "${PYTHON:-python3}" -m venv "$venv"
uv export --project "$root" --locked --no-dev --no-emit-project --no-hashes --quiet \
    --output-file "$venv/still-lock.txt"
"$venv/bin/python" -m pip install --quiet -r "$venv/still-lock.txt" -r "$root/evals/requirements.txt"
"$venv/bin/python" "$root/scripts/dev/verify_eval_env.py"
