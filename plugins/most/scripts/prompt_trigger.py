#!/usr/bin/env python3
"""Offline routing hint and per-chat guard for prompt handoffs."""

import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unicodedata


MAX_INPUT_BYTES = 262144
SKILL = Path(__file__).resolve().parents[1] / "skills" / "make-prompt" / "SKILL.md"
START = r"(?:^|[.!?;:\n])\s*(?:[-*]\s*)?"
PREFIX = (
    r"(?:(?:пожалуйста[,\s]+|давай\s+|мне\s+|нам\s+|нужно\s+|надо\s+|"
    r"хочу(?:,?\s*чтобы\s+ты)?\s+|можешь(?:\s+ли\s+ты)?\s+|"
    r"можете(?:\s+ли\s+вы)?\s+|ты\s+|вы\s+)){0,4}"
)
VERB = (
    r"(?:сделай(?:те)?|сделать|сделал|напиши(?:те)?|написать|написал|"
    r"создай(?:те)?|создать|создал|придумай(?:те)?|придумать|придумал|"
    r"подготовь(?:те)?|подготовить|составь(?:те)?|составить|"
    r"перепиши(?:те)?|переписать|доработай(?:те)?|доработать|"
    r"улучши(?:те)?|улучшить|переработай(?:те)?|переработать)"
)
MODIFIERS = (
    r"(?:\s*,?\s+(?:пожалуйста|мне|нам|только|этот|мой|готовый|новый|точный|"
    r"сильный|творческий|креативный|технический|подробный|детальный|"
    r"максимально|очень|более|хороший|лучший|финальный|идеальный|"
    r"качественный|профессиональный)){0,7}"
)
NOUN = r"промп?т(?:а|ы|ов|у|ом|е)?\b"
RU_REQUEST = re.compile(START + PREFIX + VERB + MODIFIERS + r"\s*,?\s+" + NOUN, re.I)
RU_NEED = re.compile(
    START + r"(?:(?:мне|нам)\s+)?(?:нужен|нужны|хочу)" + MODIFIERS + r"\s+" + NOUN,
    re.I,
)
EN_REQUEST = re.compile(
    START + r"(?:(?:please|can you|could you|i need you to)\s+)?"
    r"(?:write|create|make|draft|rewrite|improve|refine)\s+"
    r"(?:(?:me|a|an|the|this|my|creative|technical|detailed|better)\s+){0,5}prompts?\b",
    re.I,
)
EXPLICIT = re.compile(
    START + r"(?:используй\s+|use\s+)?\$(?:most:)?make-prompt(?=\s|[,:]|$)",
    re.I,
)
CONTINUE = re.compile(
    r"(?:^|[.!?;:,\n])\s*(?:[-*]\s*)?"
    r"(?:(?:пожалуйста[,\s]+|давай\s+|теперь\s+|можешь\s+|можешь\s+ли\s+ты\s+)){0,4}"
    r"(?:продолжай(?:те)?|продолжи(?:те)?|продолжаем|продолжим|реализуй(?:те)?|реализовать|"
    r"выполни(?:те)?|выполняй(?:те)?|начинай(?:те)?|начать|запускай(?:те)?|"
    r"запусти(?:те)?|поехали|continue|implement|execute|proceed)\b",
    re.I,
)
MODEL_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,79}\Z")
READ_ONLY = re.compile(
    r"(?:ничего\s+не\s+(?:записывай|сохраняй|меняй|изменяй)|"
    r"не\s+(?:записывай|сохраняй|меняй|изменяй)(?:\s+файлы)?|"
    r"без\s+(?:записи|изменений)|только\s+(?:чтение|читай|прочитай)|"
    r"read.only|do\s+not\s+(?:write|modify)|don't\s+(?:write|modify))", re.I)
OVERRIDE = re.compile(
    r"\b(?:продолжай|продолжи|реализуй|выполни|выполняй|continue|proceed|implement)\s+"
    r"(?:пожалуйста[,\s]+)?(?:в|на|in|on)\s+(?:текущей\s+модели|этой\s+модели|"
    r"выбранной\s+модели|той\s+же\s+модели|дорогой\s+модели|"
    r"(?:gpt[- ]?[\d.]+[- ]?)?astra|(?:the\s+)?current\s+model)\b", re.I)
MAX_PENDING_AGE = 7 * 24 * 60 * 60
ONLY_PROMPT = re.compile(r"\b(?:только|лишь|исключительно)\s+(?:сам\s+)?промп?т\b|"
                         r"\b(?:prompt\s+only|only\s+(?:the\s+)?prompt)\b", re.I)
SWITCH = re.compile(r"переключ|смен(?:и|ить|ите|ите)|выбер(?:и|ите)|выбрать|switch|choose|select", re.I)
CONTINUE_WORD = re.compile(r"продолж|реализуй|continue|proceed", re.I)
EXTERNAL_CHAT = re.compile(r"нов(?:ый|ом)\s+чат|друго(?:й|м)\s+(?:чат|сервис)|"
                           r"откро(?:й|йте|ешь)|open\b|new\s+chat", re.I)
TRANSFER = re.compile(r"встав|скопир|перенес|передай|передайте|\bpaste\b|\bcopy\b", re.I)


def has_handoff(message):
    """Only the author's own text can tell the user what to do next."""
    prose = []
    fence = None
    for line in message.splitlines():
        line = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(0)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        elif fence is None and not line.startswith(">"):
            prose.append(line)
    advice = "\n".join(prose)
    return bool((SWITCH.search(advice) and CONTINUE_WORD.search(advice)) or
                OVERRIDE.search(advice) or
                (EXTERNAL_CHAT.search(advice) and TRANSFER.search(advice)))


def request_text(prompt):
    """Ignore quoted examples and code; never promote their contents to context."""
    prompt = unicodedata.normalize("NFKC", html.unescape(prompt))
    lines = []
    fence = None
    for line in prompt.splitlines():
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            value = marker.group(0)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            continue
        if fence is None and not stripped.startswith(">"):
            lines.append(line)
    text = "\n".join(lines)
    for pattern in (r"`+[^`\n]*`+", r"«[^»]*»", r'“[^”]*”', r'"[^"\n]*"', r"'[^'\n]*'"):
        text = re.sub(pattern, " ", text)
    return text


def is_prompt_request(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    text = request_text(prompt)
    return any(pattern.search(text) for pattern in (EXPLICIT, RU_REQUEST, RU_NEED, EN_REQUEST))


def is_continuation(prompt):
    return bool(isinstance(prompt, str) and CONTINUE.search(request_text(prompt)))


def _state_path(data_dir, session_id):
    if not data_dir or not isinstance(session_id, str) or not 1 <= len(session_id) <= 200:
        return None
    return Path(data_dir) / "handoffs" / (hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json")


def _read_pending(path, now):
    if path is None:
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 2048:
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), encoding="utf-8") as stream:
            value = json.load(stream)
        model, created = value.get("author_model"), value.get("created_at")
        if (value.get("version") != 1 or not isinstance(model, str) or
                not MODEL_ID.fullmatch(model) or isinstance(created, bool) or
                not isinstance(created, (int, float)) or now - created > MAX_PENDING_AGE or
                created - now > 300):
            return None
        return value
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return None


def _write_pending(path, model, now, only_prompt=False):
    if path is None:
        return False
    temp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.is_symlink() or path.is_symlink():
            return False
        fd, temp_name = tempfile.mkstemp(prefix=".handoff-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "author_model": model, "created_at": now,
                       "only_prompt": bool(only_prompt)}, stream)
            stream.write("\n")
        os.replace(temp_name, path)
        return True
    except OSError:
        return False
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _clear_pending(path):
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def hook(payload, data_dir=None, now=None):
    if not isinstance(payload, dict):
        return {}
    event = payload.get("hook_event_name")
    state_dir = data_dir if data_dir is not None else os.environ.get("PLUGIN_DATA")
    path = _state_path(state_dir, payload.get("session_id"))
    stamp = time.time() if now is None else now
    pending = _read_pending(path, stamp)
    if event == "Stop":
        message = payload.get("last_assistant_message")
        if (not pending or pending.get("only_prompt") or payload.get("stop_hook_active") or
                not isinstance(message, str) or not message.strip()):
            return {}
        final = message.strip()
        # Do not interfere with a clarification or a structured output schema.
        if len(final) < 300 and final.endswith("?"):
            return {}
        if final.startswith(("{", "[")):
            try:
                if isinstance(json.loads(final), (dict, list)):
                    return {}
            except (ValueError, RecursionError):
                pass
        is_prompt_answer = bool(re.search(r"промп?т|\bprompt\b", final, re.I))
        if is_prompt_answer and not has_handoff(final):
            return {"decision": "block", "reason": (
                "МОСТ: если готовый промпт уже создан, выдай его полностью "
                "в финальном ответе, кратко порекомендуй доступную модель и попроси "
                "переключить весь чат и написать «продолжай». Для внешнего исполнителя "
                "укажи, куда вставить блок и какие вложения перенести. После этого остановись. "
                "Не выполняй задание сейчас. Если необходим один уточняющий вопрос, "
                "задай его вместо выдумывания ответа. Это единственная проверка.")}
        return {}
    if event != "UserPromptSubmit":
        return {}
    prompt = payload.get("prompt")
    authoring = is_prompt_request(prompt)
    continuation = is_continuation(prompt)
    model = payload.get("model")
    active_model = model if isinstance(model, str) and MODEL_ID.fullmatch(model) else "unknown"
    can_write = payload.get("permission_mode") != "plan" and not (
        isinstance(prompt, str) and READ_ONLY.search(prompt))

    guard_recorded = False
    if authoring and can_write:
        guard_recorded = _write_pending(path, active_model, stamp,
                                        bool(ONLY_PROMPT.search(request_text(prompt))))
    elif continuation and pending:
        override = bool(OVERRIDE.search(request_text(prompt)))
        if not override and (active_model == "unknown" or pending["author_model"] == "unknown"):
            return {"decision": "block", "reason": (
                "МОСТ: не удалось проверить модель чата. Переключите чат на "
                "рекомендованную модель и напишите «продолжай в выбранной модели». "
                "Это подтверждение применится только к текущей передаче.")}
        if not override and active_model == pending["author_model"]:
            return {"decision": "block", "reason": (
                "МОСТ: по-прежнему выбрана модель, создававшая промпт. "
                "Переключите модель всего чата на рекомендованного исполнителя и напишите "
                "«продолжай». Если вы намеренно хотите выполнить работу в той же модели, "
                "напишите «продолжай в текущей модели».")}
        if can_write:
            _clear_pending(path)
    elif not authoring and not continuation:
        if pending and can_write:
            _clear_pending(path)
        return {}

    if not authoring and not continuation:
        return {}
    # Only fixed, plugin-owned guidance and a validated model ID enter context.
    # Never include the prompt, cwd, transcript, session ID, or other payload strings.
    context = (
        "МОСТ: this may be a prompt-authoring request or its continuation. "
        "Confirm that intent from the user's actual message; quoted examples, discussion, "
        "negation, and requests to execute an existing prompt are not prompt-authoring. "
        f"For prompt-authoring or continuation after its handoff, read the skill at {SKILL}. "
        f"The Codex hook reported the active model as {active_model}; unknown means unverified. "
        f"The per-chat handoff guard was {'recorded' if guard_recorded else 'not recorded for this turn'}; "
        "do not claim a mechanical guard is active if it was not recorded. "
        "The current selected root model must itself create an original coherent concept "
        "and immediately translate it into a complete, precise implementation prompt "
        "in the same turn, without an intermediate approval or another model. "
        "Put the complete prompt and model recommendation together in the final response. "
        "Ask the user to switch the whole chat or paste the prompt into a new chat "
        "with the external executor, and stop before implementation. "
        "On an actual continuation after such a handoff, use the existing prompt and check "
        "the active model against the handoff; do not silently keep executing on the costly "
        "author model. An explicit later user override takes precedence. "
        "This hook does not prove model availability or price, switch models, grant "
        "permissions, or override the user's explicitly requested output format."
    )
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            result = {}
        else:
            result = hook(json.loads(raw))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        result = {}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
