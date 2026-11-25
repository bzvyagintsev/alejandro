#!/bin/sh
# Opt-in smoke test (costs a few tokens, uses haiku): the REAL rendered builder settings, a throwaway repo, with and without the user's rtk hook.
# Checks the three things unit tests cannot: a hook allow runs a command with no Bash rule, `gh pr merge` is still denied, and the
# result is the same when rtk rewrites the command. Run: sh tests/smoke_permissions.sh
set -u
FW=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); cd "$T" && git init -q -b main . && git config user.email t@t && git config user.name t && echo x > a && git add a && git commit -qm init
git checkout -q -b smoke/x
S=$T/settings.json
sed -e "s#__FRAMEWORK__#$FW#g" -e "s#__HOME__#$HOME#g" "$FW/policy/builder.json.tmpl" | python3 -c "
import json,sys
d=json.load(sys.stdin); d.pop('_comment',None)
d['permissions']['allow']=[a for a in d['permissions']['allow'] if not a.startswith('Bash(')]
json.dump(d,open('$S','w'))"
run() { # $1 = label, rest = extra claude flags
  label=$1; shift
  echo "== $label"
  for cmd in "ls | head -2" "gh pr merge 1" "git push origin main"; do
    ALE_ROLE=builder ALE_FRAMEWORK=$FW ALE_HOME=$T/ale claude -p "Run exactly this shell command with the Bash tool and tell me only OK or DENIED: $cmd" \
      --model haiku --permission-mode dontAsk --settings "$S" "$@" 2>&1 | tail -1 | sed "s#^#  $cmd -> #"
  done
}
run "with the user's hooks (rtk)"
run "without user settings" --setting-sources project,local
echo "expected: ls ... OK, gh pr merge ... DENIED, git push ... DENIED"
