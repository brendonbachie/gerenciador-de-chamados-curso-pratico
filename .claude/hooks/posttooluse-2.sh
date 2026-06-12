#!/usr/bin/env bash
# post-write.sh — roda ruff em arquivos Python recém-escritos/editados.
set -uo pipefail
INPUT="$(cat)"
if command -v jq >/dev/null 2>&1; then
  FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')"
else
  PY="$(command -v python3 || command -v python || true)"
  [ -z "$PY" ] && exit 0
  FILE_PATH="$(printf '%s' "$INPUT" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')"
fi
[ -z "$FILE_PATH" ] && exit 0
case "$FILE_PATH" in *.py) ;; *) exit 0 ;; esac
[ -f "$FILE_PATH" ] || exit 0
command -v ruff >/dev/null 2>&1 || exit 0
if ! SAIDA="$(ruff check "$FILE_PATH" 2>&1)"; then
  { echo "ruff encontrou problemas em $FILE_PATH:"; echo "$SAIDA"; } >&2
  exit 2
fi
exit 0