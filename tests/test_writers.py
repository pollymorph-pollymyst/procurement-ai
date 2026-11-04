"""Выбор сервиса для «Комментария аналитика». Сеть не нужна: сервис подменяется. Запуск: python -m pytest -q"""
from __future__ import annotations

import dataclasses
import json
import shutil

import pytest

from procurement import commentary, writers
from procurement.config import ROOT, load_settings
from procurement.settings import SettingsError

KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GIGACHAT_AUTH_KEY", "LOCAL_API_KEY")


@pytest.fixture()
def sb(tmp_path, monkeypatch):
    """Песочница: своя копия config.toml и prompts, пустой .env, ключи окружения убраны."""
    shutil.copy(ROOT / "config.toml", tmp_path / "config.toml")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    for k in KEYS:
        monkeypatch.setenv(k, "")  # запомнить «не было», чтобы после теста убрать
        monkeypatch.delenv(k)
    return dataclasses.replace(load_settings(tmp_path / "config.toml"), root=tmp_path)


def test_not_chosen_means_template(sb):
    st = writers.state(sb)
    assert st["chosen"] is False and writers.active(sb) is None


def test_save_openai_keeps_key_out_of_config(sb):
    writers.save(sb, {"service": "openai", "base": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": "sk-test-123"})
    cfg = (sb.root / "config.toml").read_text(encoding="utf-8")
    assert '"сервис" = "openai"' in cfg and "sk-test-123" not in cfg
    assert "OPENAI_API_KEY=sk-test-123" in (sb.root / ".env").read_text(encoding="utf-8")
    st = writers.state(sb)
    assert st["chosen"] and st["has_key"]["OPENAI_API_KEY"] and "sk-test-123" not in json.dumps(st)
    a = writers.active(sb)
    assert a["base"] == "https://api.deepseek.com/v1" and a["model"] == "deepseek-chat"


def test_save_validates(sb):
    with pytest.raises(SettingsError):
        writers.save(sb, {"service": "openai", "base": "api.openai.com", "model": "x", "key": "k"})
    with pytest.raises(SettingsError):
        writers.save(sb, {"service": "claude"})  # без ключа
    writers.save(sb, {"service": "local", "base": "http://127.0.0.1:11434/v1", "model": "qwen3"})  # ключ не нужен
    writers.save(sb, {"service": "нет"})
    assert writers.active(sb) is None


def test_openai_request_shape(sb, monkeypatch):
    seen = {}

    def fake_http(url, data, headers, root, service):
        seen.update(url=url, body=json.loads(data), auth=headers["Authorization"])
        return {"choices": [{"message": {"content": "готово"}}]}
    monkeypatch.setattr(writers, "_http", fake_http)
    a = {"service": "openai", "base": "https://api.deepseek.com/v1/", "model": "deepseek-chat", "key": "sk-1", "scope": ""}
    assert writers.chat(a, "sys", "user", sb.root) == "готово"
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions" and seen["auth"] == "Bearer sk-1"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"} and seen["body"]["response_format"]["type"] == "json_object"


def test_gigachat_gets_token_first(sb, monkeypatch):
    calls = []

    def fake_http(url, data, headers, root, service):
        calls.append((url, headers["Authorization"]))
        if "oauth" in url:
            return {"access_token": "tok"}
        return {"choices": [{"message": {"content": "готово"}}]}
    monkeypatch.setattr(writers, "_http", fake_http)
    a = {"service": "gigachat", "base": "https://gigachat.devices.sberbank.ru/api/v1", "model": "GigaChat-2-Max", "key": "QUJD", "scope": "GIGACHAT_API_PERS"}
    writers.chat(a, "sys", "user", sb.root)
    assert calls[0] == ("https://ngw.devices.sberbank.ru:9443/api/v2/oauth", "Basic QUJD") and calls[1][1] == "Bearer tok"


def test_parse_json_variants():
    good = {"headline": "Заказать", "summary": ["a"], "actions": [], "warnings": [], "advice": ["b"]}
    assert writers.parse_json(json.dumps(good, ensure_ascii=False)) == good
    assert writers.parse_json("Вот ответ:\n```json\n" + json.dumps(good) + "\n```") == good
    assert writers.parse_json("<think>…</think>" + json.dumps(good))["headline"] == "Заказать"
    with pytest.raises(ValueError):
        writers.parse_json('{"summary": []}')


@pytest.fixture(scope="module")
def result():
    from procurement.data import load
    from procurement.pipeline import compute
    s = load_settings()
    return compute(load(s), s, with_backtest=False)


def test_service_comment_used_only_if_numbers_check(sb, result, monkeypatch):
    writers.save(sb, {"service": "local", "base": "http://127.0.0.1:11434/v1", "model": "qwen3"})
    rules = commentary.rule_based(commentary.facts(result))
    monkeypatch.setattr(writers, "chat", lambda *a, **k: json.dumps({**rules, "source": None}, ensure_ascii=False))
    out = commentary.make(result, sb)
    assert out["source"] == "local" and out["headline"] == rules["headline"]

    monkeypatch.setattr(writers, "chat", lambda *a, **k: json.dumps({"headline": "Закажите 987654 штук", "summary": [], "actions": [], "warnings": []}))
    out = commentary.make(result, sb)
    assert out.get("source") != "local" and "987654" in out.get("note", "")  # выдуманное число — шаблон и причина


def test_service_down_falls_back_to_template(sb, result, monkeypatch):
    writers.save(sb, {"service": "local", "base": "http://127.0.0.1:11434/v1", "model": "qwen3"})

    def down(*a, **k):
        raise writers.WriterError("Программа на этом компьютере не отвечает.")
    monkeypatch.setattr(writers, "chat", down)
    out = commentary.make(result, sb)
    assert out["headline"] and "не отвечает" in out["note"]


def test_service_set_by_hand_uses_its_own_model(sb, monkeypatch):
    """Сервис вписан в config.toml вручную, без модели: берётся версия этого сервиса, а не Claude."""
    from procurement import settings as st
    st.save({("комментарий", "сервис"): "gigachat"}, sb.root / "config.toml")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "test-auth")
    a = writers.active(sb)
    assert a["service"] == "gigachat" and a["model"] == "GigaChat-2-Max"


def test_not_chosen_uses_any_service_with_key(sb, monkeypatch):
    """Выбора ещё нет (например, в GitHub Actions): подходит любой сервис, для которого задан ключ."""
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "test-auth")
    assert writers.active(sb)["service"] == "gigachat"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert writers.active(sb)["service"] == "claude"  # Claude — первым, как в списке


def test_not_chosen_openai_needs_model(sb, monkeypatch):
    """Для ChatGPT/DeepSeek… одного ключа мало: без версии сервис не угадать — пишем по шаблону."""
    from procurement import settings as st
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert writers.active(sb) is None
    st.save({("комментарий", "адрес"): "https://api.deepseek.com/v1", ("комментарий", "модель"): "deepseek-chat"}, sb.root / "config.toml")
    a = writers.active(sb)
    assert a["service"] == "openai" and a["model"] == "deepseek-chat"
