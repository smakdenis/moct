#!/bin/bash
set -euo pipefail

prompt_studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
prompt_studio_codex="$(command -v codex || true)"
if [ -z "$prompt_studio_codex" ]; then
  prompt_studio_codex="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
fi
if [ ! -x "$prompt_studio_codex" ]; then
  echo "Не найден Codex. Откройте приложение и установите его командную строку."
  exit 1
fi

"$prompt_studio_codex" plugin marketplace add "$prompt_studio_dir" --json
"$prompt_studio_codex" plugin add most@most --json
"$prompt_studio_codex" plugin list --marketplace most --json
echo "Проверьте enabled: true выше. Начните новый чат; для обработчика подтвердите доверие в Settings → Hooks."
