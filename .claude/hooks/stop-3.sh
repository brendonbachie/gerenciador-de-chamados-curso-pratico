#!/usr/bin/env bash
# stop.sh — ao fim do turno, roda a suíte de testes se houver testes e avisa.
set -uo pipefail
INPUT="$(cat 2>/dev/null || true)"
if command -v pytest >/dev/null 2>&1 && [ -d tests ]; then
  if ! pytest -q >/dev/null 2>&1; then
    echo "[stop.sh] Atenção: a suíte de testes está falhando." >&2
  else
    echo "[stop.sh] Testes OK." >&2
  fi
fi
exit 0