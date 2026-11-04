"""Кто пишет «Комментарий аналитика»: внешний сервис или сама программа по шаблону.

Сервисы:
- claude   — Claude (Anthropic), ключ ANTHROPIC_API_KEY;
- openai   — любой сервис с OpenAI-совместимым API: ChatGPT, DeepSeek, YandexGPT, OpenRouter… (OPENAI_API_KEY);
- gigachat — GigaChat (Сбер), ключ авторизации GIGACHAT_AUTH_KEY;
- local    — на этом компьютере: Ollama, LM Studio (OpenAI-совместимый, ключ не нужен);
- нет      — по шаблону, без внешних сервисов.

Расчёт от выбора не зависит: сервис пишет только текст, и каждое число в нём сверяется с расчётом
(commentary.check_numbers). Ключи хранятся только в .env и наружу (в дашборд, в config.toml) не попадают.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import settings as st
from .config import Settings

SECTION = "комментарий"

SERVICES = [
    {"id": "claude", "title": "Claude (Anthropic)", "key_env": "ANTHROPIC_API_KEY", "needs_key": True,
     "model": "claude-opus-5-5", "base": "",
     "help": "Ключ: console.anthropic.com → API Keys. Пишет лучше всех, но сервис зарубежный.",
     "key_help": "Начинается с sk-ant-"},
    {"id": "openai", "title": "ChatGPT, DeepSeek, YandexGPT и другие", "key_env": "OPENAI_API_KEY", "needs_key": True,
     "model": "", "base": "https://api.openai.com/v1",
     "help": "Любой сервис, совместимый с OpenAI. Выберите его из списка — адрес подставится сам.",
     "presets": [
         {"title": "ChatGPT (OpenAI)", "base": "https://api.openai.com/v1", "model": "", "model_hint": "Например, gpt-5-mini"},
         {"title": "DeepSeek", "base": "https://api.deepseek.com/v1", "model": "deepseek-chat", "model_hint": "Обычно deepseek-chat"},
         {"title": "YandexGPT (Yandex Cloud)", "base": "https://llm.api.cloud.yandex.net/v1", "model": "",
          "model_hint": "Вида gpt://<ID каталога>/yandexgpt/latest"},
         {"title": "OpenRouter (много сервисов по одному ключу)", "base": "https://openrouter.ai/api/v1", "model": "",
          "model_hint": "Как на openrouter.ai/models"},
         {"title": "Другой — укажу адрес сам", "base": "", "model": "", "model_hint": ""},
     ]},
    {"id": "gigachat", "title": "GigaChat (Сбер)", "key_env": "GIGACHAT_AUTH_KEY", "needs_key": True,
     "model": "GigaChat-2-Max", "base": "https://gigachat.devices.sberbank.ru/api/v1",
     "help": "Ключ авторизации: developers.sber.ru → GigaChat API → «Получить ключ». Данные остаются в России.",
     "key_help": "«Authorization key» из личного кабинета",
     "scopes": [["GIGACHAT_API_PERS", "Физическое лицо"], ["GIGACHAT_API_B2B", "Компания, оплата пакетами"],
                ["GIGACHAT_API_CORP", "Компания, оплата по факту"]]},
    {"id": "local", "title": "На этом компьютере (Ollama, LM Studio)", "key_env": "LOCAL_API_KEY", "needs_key": False,
     "model": "", "base": "http://127.0.0.1:11434/v1",
     "help": "Данные не уходят с компьютера. Программа Ollama или LM Studio должна быть установлена и запущена.",
     "presets": [
         {"title": "Ollama", "base": "http://127.0.0.1:11434/v1", "model": "", "model_hint": "Название из «ollama list», например qwen3"},
         {"title": "LM Studio", "base": "http://127.0.0.1:1234/v1", "model": "", "model_hint": "Как в LM Studio → Developer"},
     ]},
    {"id": "нет", "title": "Без внешнего сервиса", "key_env": "", "needs_key": False, "model": "", "base": "",
     "help": "Комментарий собирается по шаблону из расчёта. Работает всегда, без интернета и ключей."},
]
BY_ID = {x["id"]: x for x in SERVICES}
TIMEOUT = 180  # сервисы, которые «думают», отвечают до пары минут


class WriterError(Exception):
    """Понятная пользователю ошибка подключения к сервису."""


# ------------------------------------------------------------------ .env
def env_path(root: Path) -> Path:
    return root / ".env"


def load_env(root: Path) -> None:
    """.env главнее окружения: ключ, сохранённый в «Управлении», сразу действует и в пересчёте."""
    p = env_path(root)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                os.environ[k.strip()] = v


def save_key(root: Path, name: str, value: str) -> None:
    if not re.fullmatch(r"[A-Z_]+", name):
        raise st.SettingsError("Неизвестный ключ.")
    value = value.strip()
    if any(c in value for c in "\r\n\"'") or len(value) > 4000:
        raise st.SettingsError("Ключ выглядит неправильно: скопируйте его ещё раз, целиком, без кавычек.")
    p = env_path(root)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    line = f"{name}={value}"
    if re.search(rf"(?m)^\s*{name}\s*=.*$", text):
        text = re.sub(rf"(?m)^\s*{name}\s*=.*$", lambda _: line, text)
    else:
        text = (text.rstrip("\r\n") + "\n" if text else "") + line + "\n"
    p.write_text(text, encoding="utf-8")
    os.environ[name] = value


def has_key(root: Path, name: str) -> bool:
    load_env(root)
    return bool(name and os.environ.get(name))


# ------------------------------------------------------------------ настройки
def config(s: Settings) -> dict:
    cp = s.root / "config.toml"
    raw = (st.read_raw(cp).get(SECTION, {}) if cp.exists() else {}) or {}
    sid = str(raw.get("сервис", "") or "")
    # модель и адрес пустые — значит, по умолчанию для выбранного сервиса (у каждого своя версия)
    return {"service": sid, "base": str(raw.get("адрес", "") or ""), "model": str(raw.get("модель", "") or ""),
            "scope": str(raw.get("доступ", "") or "GIGACHAT_API_PERS"), "mode": str(raw.get("режим", s.commentary_mode))}


def _ready(c: dict, svc: dict) -> bool:
    """Хватает ли сведений, чтобы обратиться к сервису без выбора в дашборде."""
    return bool(svc["key_env"] and os.environ.get(svc["key_env"])
                and (c["model"] or svc["model"]) and (c["base"] or svc["base"] or svc["id"] == "claude"))


def active(s: Settings) -> dict | None:
    """Какой сервис писать будет сейчас (None — по шаблону)."""
    c = config(s)
    load_env(s.root)
    sid = c["service"]
    if not sid:  # выбор ещё не сделан (например, в GitHub Actions): первый сервис из списка, для которого задан ключ
        if c["mode"] == "шаблон":
            return None
        sid = next((x["id"] for x in SERVICES if x["id"] not in ("нет", "local") and _ready(c, x)), "")
        if not sid:
            return None
    if sid == "нет" or sid not in BY_ID:
        return None
    svc = BY_ID[sid]
    return {**c, "service": sid, "base": c["base"] or svc["base"], "model": c["model"] or svc["model"],
            "key": os.environ.get(svc["key_env"], "") if svc["key_env"] else ""}


def state(s: Settings) -> dict:
    c = config(s)
    return {"service": c["service"], "chosen": bool(c["service"]), "base": c["base"], "model": c["model"],
            "scope": c["scope"], "services": SERVICES,
            "has_key": {x["key_env"]: has_key(s.root, x["key_env"]) for x in SERVICES if x["key_env"]}}


def save(s: Settings, body: dict) -> dict:
    sid = str(body.get("service", ""))
    if sid not in BY_ID:
        raise st.SettingsError("Выберите, кто будет писать комментарий.")
    svc = BY_ID[sid]
    base, model = str(body.get("base") or "").strip(), str(body.get("model") or "").strip()
    if sid in ("openai", "local"):
        if not re.match(r"^https?://[^\s]+$", base):
            raise st.SettingsError("Укажите адрес сервиса: он начинается с https:// (или http://localhost для программ на этом компьютере).")
        if not model:
            raise st.SettingsError("Укажите версию — её название есть в документации сервиса.")
    key = str(body.get("key") or "")
    if key.strip():
        save_key(s.root, svc["key_env"], key)
    if svc["needs_key"] and not has_key(s.root, svc["key_env"]):
        raise st.SettingsError("Вставьте ключ доступа к сервису.")
    vals = {(SECTION, "сервис"): sid, (SECTION, "режим"): "шаблон" if sid == "нет" else "авто"}
    if sid != "нет":
        vals[(SECTION, "адрес")] = base if sid in ("openai", "local") else ""
        vals[(SECTION, "модель")] = model or svc["model"]
    if sid == "gigachat":
        scope = str(body.get("scope") or "GIGACHAT_API_PERS")
        if scope not in {x[0] for x in svc["scopes"]}:
            raise st.SettingsError("Неизвестный тип доступа GigaChat.")
        vals[(SECTION, "доступ")] = scope
    st.save(vals, s.root / "config.toml")
    return state(s)


# ------------------------------------------------------------------ запросы
def _ssl_context(root: Path) -> ssl.SSLContext:
    ctx = ssl.create_default_context()  # на Windows — системные сертификаты
    for p in sorted((root / "certs").glob("*.*")) if (root / "certs").exists() else []:
        if p.suffix.lower() in (".crt", ".cer", ".pem"):
            try:
                ctx.load_verify_locations(cafile=str(p))
            except (ssl.SSLError, OSError):
                pass
    return ctx


def _http(url: str, data: bytes, headers: dict, root: Path, service: str) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context(root)) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
            err = body.get("error", body)
            detail = str(err.get("message") if isinstance(err, dict) else err)[:200]
        except Exception:  # noqa: BLE001
            pass
        if e.code in (401, 403):
            raise WriterError("Сервис не принял ключ. Проверьте, что он скопирован целиком и не отозван.") from None
        if e.code == 404:
            raise WriterError("Не найдены адрес или версия. Проверьте их по документации сервиса." + (f" Ответ: {detail}" if detail else "")) from None
        if e.code == 402 or e.code == 429:
            raise WriterError("Сервис отказал: закончились деньги на счёте или превышен лимит запросов." + (f" Ответ: {detail}" if detail else "")) from None
        raise WriterError(f"Сервис вернул ошибку {e.code}." + (f" {detail}" if detail else "")) from None
    except urllib.error.URLError as e:
        r = e.reason
        if isinstance(r, ssl.SSLError) or "CERTIFICATE" in str(r).upper():
            if service == "gigachat":
                raise WriterError("Нет сертификата Минцифры, без него GigaChat не открывается. Скачайте «Сертификат для Windows» "
                                  "на gosuslugi.ru/crt и установите (или положите файл .crt в папку certs программы).") from None
            raise WriterError("Не удалось проверить сертификат сервиса.") from None
        if service == "local":
            raise WriterError("Программа на этом компьютере не отвечает. Запустите Ollama или LM Studio и проверьте адрес.") from None
        raise WriterError("Нет связи с сервисом. Проверьте интернет и адрес.") from None
    except TimeoutError:
        raise WriterError("Сервис долго не отвечает. Попробуйте ещё раз позже.") from None
    except json.JSONDecodeError:
        raise WriterError("Сервис ответил не так, как ожидалось. Проверьте адрес.") from None


def _gigachat_token(a: dict, root: Path) -> str:
    key = a["key"].strip()
    if ":" in key:  # дали «Client ID:Client Secret» вместо готового ключа
        key = base64.b64encode(key.encode()).decode()
    out = _http("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", f"scope={a['scope']}".encode(),
                {"Authorization": f"Basic {key}", "RqUID": str(uuid.uuid4()), "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"}, root, "gigachat")
    if not out.get("access_token"):
        raise WriterError("GigaChat не выдал доступ. Проверьте ключ и тип доступа.")
    return out["access_token"]


def chat(a: dict, system: str, user: str, root: Path, max_tokens: int = 4000, want_json: bool = True) -> str:
    """Один запрос к выбранному сервису, ответ — текст."""
    sid = a["service"]
    if sid == "claude":
        try:
            import anthropic
        except ImportError:
            raise WriterError("Не установлена библиотека anthropic. Запустите «Открыть закупки.bat» ещё раз — она поставится сама.") from None
        client = anthropic.Anthropic(api_key=a["key"], timeout=TIMEOUT)
        kw = dict(model=a["model"], max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
        if want_json:
            from .commentary import SCHEMA
            kw.update(max_tokens=16000, thinking={"type": "adaptive"},
                      output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}})
        try:
            resp = client.messages.create(**kw)
        except anthropic.AuthenticationError:
            raise WriterError("Сервис не принял ключ. Проверьте, что он скопирован целиком и не отозван.") from None
        except anthropic.NotFoundError:
            raise WriterError("Такой версии нет. Проверьте название.") from None
        except anthropic.RateLimitError:
            raise WriterError("Превышен лимит запросов. Попробуйте позже.") from None
        except anthropic.APIConnectionError:
            raise WriterError("Нет связи с сервисом. Проверьте интернет.") from None
        except anthropic.APIStatusError as e:
            raise WriterError(f"Сервис вернул ошибку {e.status_code}.") from None
        if resp.stop_reason == "refusal":
            raise WriterError("Сервис отказался отвечать.")
        return next((b.text for b in resp.content if b.type == "text"), "")

    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    body = {"model": a["model"], "messages": msgs, "max_tokens": max_tokens, "temperature": 0.3}
    if sid == "gigachat":
        token = _gigachat_token(a, root)
        url, headers = a["base"].rstrip("/") + "/chat/completions", {"Authorization": f"Bearer {token}"}
    else:
        if want_json:
            body["response_format"] = {"type": "json_object"}
        url = a["base"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {a['key'] or 'local'}"}
    headers |= {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        out = _http(url, json.dumps(body, ensure_ascii=False).encode("utf-8"), headers, root, sid)
    except WriterError as e:
        # не все совместимые сервисы знают response_format — пробуем без него
        if "response_format" in body and ("400" in str(e) or "422" in str(e)):
            body.pop("response_format")
            out = _http(url, json.dumps(body, ensure_ascii=False).encode("utf-8"), headers, root, sid)
        else:
            raise
    try:
        return out["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise WriterError("Сервис ответил не так, как ожидалось. Проверьте адрес.") from None


def parse_json(text: str) -> dict:
    """Ответ сервиса → словарь. Многие оборачивают JSON в ```json … ``` или добавляют пояснения."""
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "").strip()
    m = re.search(r"(?s)```(?:json)?\s*(\{.*?\})\s*```", text)
    if m:
        text = m.group(1)
    elif "{" in text:
        text = text[text.index("{"): text.rindex("}") + 1]
    out = json.loads(text)
    if not isinstance(out, dict):
        raise ValueError("not an object")
    for k in ("summary", "actions", "warnings", "advice"):
        v = out.get(k, [])
        out[k] = [str(x) for x in v] if isinstance(v, list) else [str(v)] if v else []
    out["headline"] = str(out.get("headline", "")).strip()
    if not out["headline"]:
        raise ValueError("no headline")
    return {k: out[k] for k in ("headline", "summary", "actions", "warnings", "advice")}


def test(s: Settings, body: dict) -> str:
    """Проверить подключение до сохранения. Возвращает короткий ответ сервиса."""
    sid = str(body.get("service", ""))
    if sid not in BY_ID or sid == "нет":
        return ""
    svc = BY_ID[sid]
    load_env(s.root)
    key = str(body.get("key") or "").strip() or (os.environ.get(svc["key_env"], "") if svc["key_env"] else "")
    if svc["needs_key"] and not key:
        raise WriterError("Вставьте ключ доступа к сервису.")
    a = {"service": sid, "key": key, "base": str(body.get("base") or "").strip() or svc["base"],
         "model": str(body.get("model") or "").strip() or svc["model"], "scope": str(body.get("scope") or "GIGACHAT_API_PERS")}
    if not a["model"]:
        raise WriterError("Укажите версию — её название есть в документации сервиса.")
    text = chat(a, "Отвечай по-русски одним словом.", "Ответь одним словом: готово.", s.root, max_tokens=300, want_json=False)
    return re.sub(r"(?s)<think>.*?</think>", "", text).strip()[:80] or "ответ получен"
