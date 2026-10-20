#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)"
exec bun "${DIR}/index.ts" "$@"
