#!/bin/sh
# Budget guard: no ale script may call an LLM or the claude binary, except `ale spawn`'s launch argv
# (it starts interactive agents on purpose; that is the only allowed mention).
cd "$(dirname "$0")/.." || exit 1
bad=$(grep -nE "anthropic|api\.openai|claude -p|--print|\"claude\", \"-p\"" bin/ale lib/*.py hooks/*.py | grep -v "tests/")
if [ -n "$bad" ]; then echo "LLM reference found:"; echo "$bad"; exit 1; fi
echo "no-llm: ok"
