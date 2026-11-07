#!/usr/bin/env sh
set -eu
exec sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/Install.sh" --uninstall "$@"
