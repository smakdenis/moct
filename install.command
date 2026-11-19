#!/bin/bash
set -euo pipefail

moct_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
moct_codex="$(command -v codex || true)"
if [ -z "$moct_codex" ]; then
  moct_codex="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
fi
if [ ! -x "$moct_codex" ]; then
  echo "Не найден Codex. Откройте приложение и установите его командную строку."
  exit 1
fi

"$moct_codex" plugin marketplace add "$moct_dir" --json
"$moct_codex" plugin add moct@moct --json
"$moct_codex" plugin list --marketplace moct --json
echo "Проверьте enabled: true выше. Начните новый чат; для обработчика подтвердите доверие в Settings → Hooks."
