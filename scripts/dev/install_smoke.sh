#!/bin/sh
# CI-only install smoke check (#1167, #1176): install Still from a clean
# HOME, start it, and require the setup page to answer HTTP 200 and the MCP
# bridge to import. Used by .github/workflows/install-smoke.yml on every
# platform job so the check itself is identical everywhere.
#
#   sh scripts/dev/install_smoke.sh installer   # one-line scripts/install.sh
#   sh scripts/dev/install_smoke.sh homebrew    # brew install jongtae/still/agentos
#
# The Homebrew mode taps this checkout's own Formula/agentos.rb, so a formula
# PR is tested before it merges (#45). Before the first Still release there is
# no installer pin and no formula; each mode reports its own absence and passes.
#
# SMOKE_HTTP / SMOKE_NULL replace the HTTP client and its null device, e.g.
# Windows curl.exe and NUL from inside WSL2 to check localhost forwarding.
set -eu

mode=${1:-installer}
port=18787
home=$(mktemp -d)
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)

case "$mode" in
  installer)
    # The formula PR of the first release runs before the installer is pinned.
    if grep -q '^AGENTOS_VERSION=""$' "$repo/scripts/install.sh"; then
      echo "no Still release is recorded yet; scripts/install.sh has nothing to install"
      exit 0
    fi
    env -i HOME="$home" PATH=/usr/bin:/bin:/usr/sbin:/sbin AGENTOS_NO_START=1 sh "$here/../install.sh"
    run() { env -i HOME="$home" PATH="$home/.local/bin:/usr/bin:/bin" "$@"; }
    python="$home/.local/share/uv/tools/personal-agentos/bin/python"
    ;;
  homebrew)
    [ -f "$repo/Formula/agentos.rb" ] || { echo "no Formula/agentos.rb yet; it is written at the first release"; exit 0; }
    # A one-commit tap holding only this checkout's formula: Homebrew taps a
    # git repository, and the PR checkout itself is a detached merge commit.
    tap=$(mktemp -d)
    mkdir "$tap/Formula"
    cp "$repo/Formula/agentos.rb" "$tap/Formula/"
    git -C "$tap" init -q -b main
    git -C "$tap" add Formula
    git -C "$tap" -c user.name=install-smoke -c user.email=install-smoke@localhost commit -q -m formula
    brew tap jongtae/still "$tap"
    brew install jongtae/still/agentos
    brew test jongtae/still/agentos
    brew list --versions agentos
    run() { env HOME="$home" "$@"; }
    python="$(brew --prefix)/opt/agentos/libexec/bin/python"
    ;;
  *) echo "usage: $0 installer|homebrew" >&2; exit 2 ;;
esac

run agentos start --no-browser --port "$port" </dev/null >"$home/start.log" 2>&1 &
server=$!
http=${SMOKE_HTTP:-curl}
code=000
for _ in $(seq 1 90); do
  code=$("$http" -s -o "${SMOKE_NULL:-/dev/null}" -w '%{http_code}' "http://127.0.0.1:$port/" || true)
  [ "$code" = "200" ] && break
  sleep 1
done
cat "$home/start.log"
echo "setup page HTTP status: $code"
kill "$server" 2>/dev/null || true
[ "$code" = "200" ]
"$python" -c "import mcp.server.stdio; print('MCP bridge import ok')"
