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
MIN_PROMPT_CHARS = 6000  # Non-whitespace characters inside the executor's prompt.
SKILL = Path(__file__).resolve().parents[1] / "skills" / "moct" / "SKILL.md"
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
SHORT_RU = (r"(?:коротк(?:ий|им|о)|кратк(?:ий|им|о)|компактн(?:ый|ым|о)|"
            r"лаконичн(?:ый|ым|о)|сжат(?:ый|ым|о)|покороче)")
SHORT_EN = r"(?:short|brief|concise|compact|terse|one[- ]line)"
MODIFIERS = (
    r"(?:\s*,?\s+(?:и\s+)?(?:пожалуйста|мне|нам|только|этот|мой|готовый|новый|точный|"
    r"сильный|творческий|креативный|технический|подробный|детальный|"
    rf"большой|длинный|разв[её]рнутый|{SHORT_RU}|"
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
    rf"(?:(?:me|only|a|an|the|this|my|creative|technical|detailed|long|expanded|better|{SHORT_EN}|\d+[- ]word)\s+){{0,7}}prompts?\b",
    re.I,
)
EXPLICIT = re.compile(
    START + r"(?:используй\s+|use\s+)?\$(?:moct:)?moct(?=\s|[,:]|$)",
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
ONLY_PROMPT = re.compile(rf"\b(?:только|лишь|исключительно)\s+(?:сам\s+)?(?:{SHORT_RU}\s+)?промп?т\b|"
                         rf"\b(?:prompt\s+only|only\s+(?:(?:a|the)\s+)?(?:{SHORT_EN}\s+)?prompt)\b", re.I)
# Scope size requests to the prompt itself. A five-word slogan still needs a full brief.
AMOUNT = r"(?:\d+(?:[ ,.–-]\d+)*|од(?:но|ну|ин|ной)|дв[ае]|три|пару|one|two|three)"
UNIT_RU = r"(?:слов\w*|знак\w*|символ\w*|токен\w*|строк\w*|строч\w*|абзац\w*|предложени\w*)"
UNIT_EN = r"(?:words?|characters?|chars?|tokens?|lines?|paragraphs?|sentences?)"
SIZE_RU = (r"(?:(?:объ[её]мом|длиной|умести|уложи)\s+)?"
           rf"(?:(?:до|в|на|из|ровно|максимум|не более|не больше|не длиннее)\s+)?{AMOUNT}\s*{UNIT_RU}")
SIZE_EN = rf"(?:(?:under|within|in|of|up to|at most|no more than|exactly)\s+)?{AMOUNT}[- ]{UNIT_EN}"
CUSTOM_LENGTH = re.compile(
    rf"\b{SHORT_RU}\s+{NOUN}|"
    rf"\b{NOUN}[\s,:—-]+(?:(?:должен быть|должен занимать|сделай|напиши)\s+)?(?:{SHORT_RU}|{SIZE_RU})\b|"
    rf"\b(?:{SHORT_EN}|{SIZE_EN})\s+prompt\b|"
    rf"\bprompt[\s,:—-]+(?:(?:should be|must be|should fit in)\s+)?(?:{SHORT_EN}|{SIZE_EN})\b|"
    rf"\b(?:ответь|пиши)\s+{SHORT_RU}\b|\b(?:keep (?:the prompt|it)|answer) (?:short|brief|concise|briefly)\b",
    re.I)
SWITCH = re.compile(r"переключ|смен(?:и|ить|ите|ите)|выбер(?:и|ите)|выбрать|switch|choose|select", re.I)
CONTINUE_WORD = re.compile(r"продолж|реализуй|continue|proceed", re.I)
EXTERNAL_CHAT = re.compile(r"нов(?:ый|ом)\s+чат|друго(?:й|м)\s+(?:чат|сервис)|"
                           r"откро(?:й|йте|ешь)|open\b|new\s+chat", re.I)
TRANSFER = re.compile(r"встав|скопир|перенес|передай|передайте|\bpaste\b|\bcopy\b", re.I)
EXECUTOR_OPENING = re.compile(r"^(?:ты|вы|твоя задача|ваша задача|создай|напиши|составь|сделай|разработай|"
                              r"you are|your task|create|write|build|design|draft)\b", re.I)


def markdown_parts(message):
    """Separate unquoted prose and complete top-level fenced blocks."""
    prose = []
    blocks = []
    fence = None
    body = []
    for line in message.splitlines():
        stripped = line.lstrip()
        if fence is not None:
            closing = stripped.rstrip()
            if len(closing) >= len(fence) and closing == fence[0] * len(closing):
                blocks.append("\n".join(body))
                fence = None
                body = []
            else:
                body.append(line)
            continue
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            fence = marker.group(0)
        elif not stripped.startswith(">"):
            prose.append(line)
    return "\n".join(prose), blocks


def has_handoff(advice):
    """Only the author's own prose can tell the user what to do next."""
    return bool((SWITCH.search(advice) and CONTINUE_WORD.search(advice)) or
                OVERRIDE.search(advice) or
                (EXTERNAL_CHAT.search(advice) and TRANSFER.search(advice)))


def request_text(prompt):
    """Ignore quoted examples and code; never promote their contents to context."""
    prompt = unicodedata.normalize("NFKC", html.unescape(prompt))
    text, _ = markdown_parts(prompt)
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


def _write_pending(path, model, now, only_prompt, default_depth):
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
                       "only_prompt": only_prompt, "default_depth": default_depth}, stream)
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
        for marker in (path, path.with_suffix(".reviewed")):
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass


def review_answer(pending, message, resumed=False):
    final = message.strip()
    # A clarification or an exact JSON response keeps its original format.
    if final.startswith(("{", "[")):
        try:
            if isinstance(json.loads(final), (dict, list)):
                return {}
        except (ValueError, RecursionError):
            pass
    prose, blocks = markdown_parts(final)
    handoff = has_handoff(prose)
    only_prompt = pending.get("only_prompt")
    if not blocks and not handoff and len(final) < 300 and final.endswith("?"):
        return {}
    if not (resumed or blocks or only_prompt or handoff or EXECUTOR_OPENING.match(prose)
            or re.search(r"промп?т|\bprompt\b", prose, re.I)):
        return {}

    corrections = []
    if pending.get("default_depth"):
        # Prompt-only replies can be plain text. In normal replies, exclude all commentary.
        body = blocks[0] if len(blocks) == 1 else ""
        if only_prompt and not blocks:
            body = prose
        size = len("".join(body.split()))
        if size < MIN_PROMPT_CHARS:
            corrections.append(
                f"В одном полном блоке задания найдено {size} знаков без пробелов. "
                f"Раскрой его до {MIN_PROMPT_CHARS} или более, ориентир 8 000–12 000. "
                "Разверни замысел, шаги, предметные примеры и проверяемые результаты. "
                "Сохрани объём, если пользователь явно задал его для самого промпта.")
    if not only_prompt and not handoff:
        corrections.append("Кратко порекомендуй доступную модель и попроси переключить весь чат "
                           "и написать «продолжай», либо укажи, куда вставить блок в новом чате.")
    if not corrections:
        return {}
    if only_prompt:
        corrections.append("Пользователь просил только блок промпта.")
    return {"decision": "block", "reason": (
        "moct: если задание готово, выдай его полностью в финальном ответе. "
        + " ".join(corrections) + " Если полный промпт уже появился выше, повтори его здесь целиком. "
        "Остановись до реализации. Если нужны исходные данные, "
        "задай один уточняющий вопрос. Это единственная проверка.")}


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
        if not pending or not isinstance(message, str) or not message.strip():
            return {}
        if payload.get("stop_hook_active") and "default_depth" not in pending:
            return {}  # A pre-upgrade marker has no record of who requested a retry.
        correction = review_answer(pending, message, resumed=bool(payload.get("stop_hook_active")))
        if correction:
            # Another hook can resume the turn after a good answer. Consume only moct's
            # own retry, atomically. Keep the same marker for the later model-switch check.
            try:
                os.replace(path, path.with_suffix(".reviewed"))
            except OSError:
                return {}
        return correction
    if event != "UserPromptSubmit":
        return {}
    if pending is None and path is not None:
        pending = _read_pending(path.with_suffix(".reviewed"), stamp)
    prompt = payload.get("prompt")
    authoring = is_prompt_request(prompt)
    continuation = is_continuation(prompt)
    model = payload.get("model")
    active_model = model if isinstance(model, str) and MODEL_ID.fullmatch(model) else "unknown"
    can_write = payload.get("permission_mode") != "plan" and not (
        isinstance(prompt, str) and READ_ONLY.search(prompt))

    guard_recorded = False
    if authoring and can_write:
        text = request_text(prompt)
        guard_recorded = _write_pending(path, active_model, stamp,
                                        bool(ONLY_PROMPT.search(text)), not CUSTOM_LENGTH.search(text))
    elif continuation and pending:
        override = bool(OVERRIDE.search(request_text(prompt)))
        if not override and (active_model == "unknown" or pending["author_model"] == "unknown"):
            return {"decision": "block", "reason": (
                "moct: не удалось проверить модель чата. Переключите чат на "
                "рекомендованную модель и напишите «продолжай в выбранной модели». "
                "Это подтверждение применится только к текущей передаче.")}
        if not override and active_model == pending["author_model"]:
            return {"decision": "block", "reason": (
                "moct: по-прежнему выбрана модель, создававшая промпт. "
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
        "moct: confirm authoring or continuation from the user's actual intent. "
        "Ignore quoted examples, discussion and negation. "
        f"Read the skill at {SKILL}. "
        f"The Codex hook reported the active model as {active_model}; unknown means unverified. "
        f"The per-chat handoff guard was {'recorded' if guard_recorded else 'not recorded for this turn'}; "
        "claim a mechanical guard only if recorded. The selected author develops the concept "
        "and writes the full executor prompt in this turn. For new and revised prompts aim for "
        f"8000–12000 non-whitespace characters (minimum {MIN_PROMPT_CHARS}) inside ONE fenced block. "
        "Include task-specific decisions, steps, worked examples and acceptance checks. "
        "Respect explicit prompt size limits; a short target artifact still needs a detailed prompt. "
        "In the final answer give the block, recommend an executor, ask to switch the chat and say "
        "continue or paste the block in a new chat, then stop. For prompt-only, omit the advice. "
        "If a hook resumes this authoring turn, repeat the full block and its advice in the LAST answer. "
        "On continuation after such a handoff use the existing prompt and check the model; "
        "do not silently keep executing on the author model. Honor explicit overrides and formats."
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
