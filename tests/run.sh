#!/bin/sh
cd "$(dirname "$0")/.." || exit 1
sh tests/no-llm.sh && python3 -m unittest discover -s tests -v 2>&1 | tail -40
