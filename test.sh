#!/bin/bash
# Convenience wrapper. The suite itself lives in tests/ as plain unittest, which
# is what .github/workflows/release.yml runs across the Python version matrix.
#
#   ./test.sh              run everything
#   ./test.sh -k Phone     run one class
#   ./test.sh -f           stop at the first failure
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python3 -m unittest discover -s tests -v "$@"
