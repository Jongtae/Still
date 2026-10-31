#!/bin/sh
# Still one-line installer for macOS, Linux and Windows (WSL2) (#1167).
#
#   curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/main/scripts/install.sh | sh
#
# Adopted, not built: the official uv installer fetches uv and uv fetches a
# managed Python, so no Homebrew, Xcode tools, system Python or git is needed.
# This script only pins what it installs, and checks each pin before running
# it: the uv release installer (which itself carries the checksums of the uv
# binaries it downloads) and the published Still release, which is the same
# GitHub tag archive the Homebrew formula uses, against the SHA-256 recorded
# in docs/release-manifest.json. A current uv already on PATH is reused as
# is, with the same trust as any other tool the owner installed; only a uv
# this script downloads is checksum-verified. `--no-config` keeps the owner's
# uv settings (indexes, offline mode) out of this install.
# tests/test_install_script.py keeps these pins equal to the newest published
# release; docs/release.en.md step 9 updates them with each release. Before
# the first Still release both release pins are empty and the script stops
# without downloading anything (#45).
set -eu

AGENTOS_VERSION="0.3.0"
AGENTOS_ARCHIVE_SHA256="5fcede8864c7c8a36417dce7988c5f81c103f3fcce8e3c50821d5c685b580209"
UV_VERSION="0.12.23"
UV_INSTALLER_SHA256="b8e6c43099ee9f9a550984d3ad56948457c689e7a99c090b35377234ac241491"
PYTHON_VERSION="3.13"

say() { printf '%s\n' "$*"; }
fail() { printf 'Still install: %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*)
    fail "Windows runs Still inside WSL2. In PowerShell run 'wsl --install', restart, open Ubuntu and run this command there. See docs/QUICKSTART.md (Windows)." ;;
  *) fail "unsupported system $(uname -s); Still supports macOS, Linux and Windows through WSL2." ;;
esac

[ -n "$AGENTOS_VERSION" ] \
  || fail "no Still release has been published yet, so there is nothing to install. To run Still from source, see https://github.com/Jongtae/Still/blob/main/docs/QUICKSTART.md#remote-host--source-installation"

download() { # url destination
  if command -v curl >/dev/null 2>&1; then curl -LsSf "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -q "$1" -O "$2"
  else fail "curl or wget is required."; fi
}

if command -v sha256sum >/dev/null 2>&1; then SHA256="sha256sum"
elif command -v shasum >/dev/null 2>&1; then SHA256="shasum -a 256"
else fail "sha256sum or shasum is required."; fi
for pin in "$UV_INSTALLER_SHA256" "$AGENTOS_ARCHIVE_SHA256"; do
  [ "${#pin}" -eq 64 ] || fail "invalid checksum pin in this installer."
done

sha256_of() { $SHA256 "$1" | cut -d' ' -f1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

# Verify the Still release first, so a bad download changes nothing on this machine.
archive="$work/agentos-$AGENTOS_VERSION.tar.gz"
say ""
say "  Still - your personal agent, on your own machine."
say ""
say "Downloading Still $AGENTOS_VERSION..."
download "https://github.com/Jongtae/Still/archive/refs/tags/v$AGENTOS_VERSION.tar.gz" "$archive"
[ "$(sha256_of "$archive")" = "$AGENTOS_ARCHIVE_SHA256" ] \
  || fail "checksum mismatch for v$AGENTOS_VERSION; nothing was installed."

# Reuse an existing uv only at the pinned minor version or newer; older uv
# releases lack the `uv tool` commands used below.
uv_is_current() {
  ver=$("$1" --version 2>/dev/null | cut -d' ' -f2)
  major=${ver%%.*}; rest=${ver#*.}; minor=${rest%%.*}
  want_major=${UV_VERSION%%.*}; want_rest=${UV_VERSION#*.}; want_minor=${want_rest%%.*}
  case "$major$minor" in ''|*[!0-9]*) return 1 ;; esac
  [ "$major" -gt "$want_major" ] || { [ "$major" -eq "$want_major" ] && [ "$minor" -ge "$want_minor" ]; }
}

if command -v uv >/dev/null 2>&1 && uv_is_current "$(command -v uv)"; then
  UV=$(command -v uv)
else
  # The uv installer uses these but does not check all of them itself; a
  # minimal system without awk otherwise fails with a misleading checksum error.
  for tool in awk tar gzip; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required to install uv; install it with your package manager and run this again."
  done
  say "Installing uv $UV_VERSION (Python package manager by Astral)..."
  download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-installer.sh" "$work/uv-install.sh"
  [ "$(sha256_of "$work/uv-install.sh")" = "$UV_INSTALLER_SHA256" ] \
    || fail "checksum mismatch for the uv $UV_VERSION installer; nothing was installed."
  sh "$work/uv-install.sh" >/dev/null || fail "the uv installer failed."
  UV="${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}/uv"
  [ -x "$UV" ] || fail "uv was installed but not found at $UV."
fi

say "Installing Still $AGENTOS_VERSION (this can take a minute)..."
"$UV" --no-config tool install --force --python "$PYTHON_VERSION" "personal-agentos[mcp-host] @ $archive" \
  >"$work/install.log" 2>&1 &
installing=$!
# #438: a dot a second, so resolving and downloading do not look stuck.
while kill -0 "$installing" 2>/dev/null; do printf '.'; sleep 1; done
printf '\n'
wait "$installing" || { tail -n 20 "$work/install.log" >&2; fail "uv could not install Still."; }
"$UV" --no-config tool update-shell >/dev/null 2>&1 || true
bin_dir=$("$UV" --no-config tool dir --bin)

say ""
say "Still $AGENTOS_VERSION is installed. Next time, open a new terminal and run: agentos start"
if [ "${AGENTOS_NO_START:-0}" = "1" ]; then exit 0; fi
say "Starting Still now (press Ctrl-C to stop)."
rm -rf "$work"; trap - EXIT INT TERM
# stdin is this piped script, so first setup reads the terminal itself when one exists.
if (: </dev/tty) 2>/dev/null; then exec "$bin_dir/agentos" start </dev/tty; fi
exec "$bin_dir/agentos" start </dev/null
