#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/local_signoff.sh [--signoff]

Run the local pre-merge checks. With --signoff, require a clean, pushed
commit and record a successful gh-signoff status only after every check passes.
Set PYTHON=/path/to/python to select the project environment.
EOF
}

signoff=false
case "${1:-}" in
  "") ;;
  --signoff) signoff=true ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
python=${PYTHON:-python3}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

run_check() {
  local label=$1
  shift
  printf '\n==> %s\n' "$label"
  "$@"
}

require_command git
require_command "$python"
require_command gitleaks

if $signoff; then
  require_command gh
  gh signoff version >/dev/null

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Refusing signoff: repository has uncommitted changes." >&2
    exit 1
  fi
  if ! push_ref=$(git rev-parse --abbrev-ref '@{push}' 2>/dev/null); then
    echo "Refusing signoff: current branch has no push remote." >&2
    exit 1
  fi
  if [[ "$(git rev-parse HEAD)" != "$(git rev-parse "$push_ref")" ]]; then
    echo "Refusing signoff: current commit is not pushed." >&2
    exit 1
  fi
fi

if ! "$python" -c 'import gbrain_ops, pytest, ruff' >/dev/null 2>&1; then
  echo "Project dev dependencies are missing for $python; install with: $python -m pip install -e '.[dev]'" >&2
  exit 1
fi

run_check "tests" "$python" -m pytest -q
run_check "lint" "$python" -m ruff check src tests scripts adapters
run_check "privacy" "$python" -m gbrain_ops.privacy_scan .
run_check "secret scan" gitleaks detect --source . --no-git --no-banner --redact

if $signoff; then
  run_check "GitHub signoff" gh signoff
fi

printf '\nLocal pre-merge checks passed.\n'