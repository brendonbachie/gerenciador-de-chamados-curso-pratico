#!/usr/bin/env bash
# pre-bash.sh — bloqueia comandos perigosos antes da execução.
set -uo pipefail
INPUT="$(cat)"
if command -v jq >/dev/null 2>&1; then
  COMMAND="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')"
else
  PY="$(command -v python3 || command -v python || true)"
  [ -z "$PY" ] && exit 0
  COMMAND="$(printf '%s' "$INPUT" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')"
fi
[ -z "$COMMAND" ] && exit 0
PADROES=(
  'rm[[:space:]]+-[a-zA-Z]*[rR][a-zA-Z]*[[:space:]]+/([[:space:]]|$)'
  'rm[[:space:]]+-[a-zA-Z]*[rR][a-zA-Z]*[[:space:]]+/\*'
  'git[[:space:]]+push[[:space:]].*--force([[:space:]]|$)'
  'mkfs(\.[a-z0-9]+)?[[:space:]]'
  'dd[[:space:]].*of=/dev/'
  '(curl|wget)[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(ba|z)?sh'
  'projetos\.json'
)
for padrao in "${PADROES[@]}"; do
  if printf '%s' "$COMMAND" | grep -Eq "$padrao"; then
    { echo "Comando bloqueado pelo hook pre-bash.sh (padrão perigoso ou dado sensível de chamados)."; echo "Padrão: $padrao"; echo "Comando: $COMMAND"; } >&2
    exit 2
  fi
done
exit 0