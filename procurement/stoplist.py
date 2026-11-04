"""Отметки закупщика «больше не заказывать».

Товар: насовсем или до даты (поставщик на паузе), с причиной. Поставщик: «больше не работаем» —
его товары нужно закупать у другого, поэтому они переходят в «Нужен поставщик».
Хранится в stoplist.json в корне проекта: отметки влияют на расчёт, поэтому публикуются вместе
с настройками и ежедневный пересчёт на GitHub их тоже учитывает. Личных данных здесь нет.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from .config import Settings

EMPTY = {"skus": {}, "suppliers": {}}


class StopError(ValueError):
    pass


def path(s: Settings) -> Path:
    return s.root / "stoplist.json"


def read(s: Settings) -> dict:
    try:
        d = json.loads(path(s).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"skus": {}, "suppliers": {}}
    out = {"skus": {}, "suppliers": {}}
    for part in out:
        for k, v in (d.get(part) or {}).items() if isinstance(d, dict) else []:
            if isinstance(v, dict):
                out[part][str(k)] = {"until": v.get("until") or None, "why": str(v.get("why") or ""),
                                     "when": v.get("when") or None}
    return out


def active(entry: dict | None, as_of: date) -> bool:
    """Отметка действует: насовсем или дата «до» ещё не наступила."""
    if not entry:
        return False
    u = entry.get("until")
    if not u:
        return True
    try:
        return date.fromisoformat(str(u)[:10]) > as_of
    except ValueError:
        return True


def _clean(v, limit: int) -> str:
    v = re.sub(r"[\x00-\x1f\x7f]", " ", str(v or "")).strip()
    if len(v) > limit:
        raise StopError(f"Причина: не длиннее {limit} знаков.")
    return v


def save(s: Settings, body: dict, known_skus: set[str], known_suppliers: set[str]) -> dict:
    """body: {"kind": "sku" | "supplier", "id", "on": bool, "until": "YYYY-MM-DD" | None, "why"}."""
    kind, ident = body.get("kind"), str(body.get("id") or "").strip()
    if kind not in ("sku", "supplier"):
        raise StopError("Неизвестная отметка.")
    if ident not in (known_skus if kind == "sku" else known_suppliers):
        raise StopError("Нет такого товара." if kind == "sku" else "Нет такого поставщика.")
    data = read(s)
    part = data["skus" if kind == "sku" else "suppliers"]
    if body.get("on"):
        until = body.get("until") or None
        if until is not None:
            if kind == "supplier":
                raise StopError("У поставщика отметка — насовсем.")
            try:
                d = date.fromisoformat(str(until)[:10])
            except ValueError:
                raise StopError("Дата «до» выглядит неверно.") from None
            if d <= date.today():
                raise StopError("Дата «до» должна быть в будущем.")
            until = d.isoformat()
        part[ident] = {"until": until, "why": _clean(body.get("why"), 120),
                       "when": datetime.now().replace(microsecond=0).isoformat()}
    else:
        part.pop(ident, None)
    path(s).write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return data
