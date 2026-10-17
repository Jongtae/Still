#!/bin/sh
# #1531: owners install with no compiler toolchain, so every supported platform
# must resolve Still's dependencies from published wheels alone. A dependency
# that drops a platform's wheel fails here instead of on an owner's machine.
set -eu
cd "$(dirname "$0")/../.."
python=$(sed -n 's/^PYTHON_VERSION="\(.*\)"$/\1/p' scripts/install.sh)
[ -n "$python" ] || { echo "PYTHON_VERSION not found in scripts/install.sh" >&2; exit 2; }
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
status=0
# musl (Alpine) builds claude-agent-sdk from its pure-Python sdist without a
# compiler; the install-smoke Alpine job covers it with a real install.
for platform in x86_64-apple-darwin aarch64-apple-darwin x86_64-unknown-linux-gnu \
                aarch64-unknown-linux-gnu; do
  # Resolve as an owner's install does, then require wheels for exactly those
  # versions: --no-build alone would quietly fall back to an older release.
  if uv --no-config pip compile pyproject.toml --extra mcp-host --python-version "$python" \
       --python-platform "$platform" --quiet -o "$out/requirements.txt" &&
     uv --no-config pip compile "$out/requirements.txt" --python-version "$python" \
       --python-platform "$platform" --no-build --quiet -o "$out/wheels.txt"; then
    echo "wheels only: $platform (Python $python)"
  else
    echo "needs a source build: $platform (Python $python)" >&2
    status=1
  fi
done
exit $status
