"""«Комментарий аналитика»: короткий текст для закупщика поверх расчёта.

Кто пишет:
- по правилам (всегда работает, без интернета и ключей);
- внешний сервис, выбранный в «Управлении»: Claude, OpenAI-совместимый, GigaChat, локальный (writers.py).
  Ответ проверяется: каждое число в тексте должно быть в фактах. Иначе — комментарий по правилам.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Settings
from .plan import MONTHS_NOM, n, ru_date

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "summary", "actions", "warnings"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ факты
def facts(result: dict, changes: dict | None = None) -> dict:
    k = result["kpi"]
    as_of = result["meta"]["as_of"]
    top_sup = [{"supplier_id": g["supplier_id"], "skus": g["skus"], "qty": g["qty"],
                "deficit_skus": g["deficit"], "arrival": ru_date(g["arrival_date"])}
               for g in result["order_now"][:5]]
    month_now = int(as_of[5:7])
    peaks = []
    for c in result["categories"]:
        ahead = (c["peak_month"] - month_now) % 12
        if c["season"][c["peak_month"] - 1] >= 1.5 and 0 < ahead <= 8:
            peaks.append({"category": c["category_name"], "peak_month": MONTHS_NOM[c["peak_month"] - 1],
                          "months_ahead": ahead, "order_now_qty": c["order_now_qty"]})
    deficit = [{"sku_id": r["sku_id"], "category": r["category_name"], "lost_units": r["lost_units"]}
               for r in sorted(result["lists"]["deficit"], key=lambda r: -r["lost_units"])[:5]]
    f = {
        "as_of": ru_date(as_of),
        "kpi": k,
        "top_suppliers": top_sup,
        "season_peaks_ahead": peaks,
        "top_deficit": deficit,
    }
    bt = result.get("backtest") or {}
    if bt.get("available") and bt.get("model_error") is not None:
        f["forecast_check"] = {"better_than_simple_average": bt["model_error"] < bt["naive_error"]}
    if changes and changes.get("available"):
        f["changes"] = {
            "prev_as_of": ru_date(changes["prev_as_of"]),
            "became_order_now": len(changes["became_order_now"]),
            "left_order_now": len(changes["left_order_now"]),
            "order_now_skus_delta": changes["kpi_delta"].get("order_now_skus", 0),
            "order_now_qty_delta": changes["kpi_delta"].get("order_now_qty", 0),
        }
    return f


# ------------------------------------------------------------------ по правилам
def _plural(x: int, one: str, few: str, many: str) -> str:
    x = abs(int(x))
    if x % 10 == 1 and x % 100 != 11:
        return one
    if 2 <= x % 10 <= 4 and not 12 <= x % 100 <= 14:
        return few
    return many


def rule_based(f: dict) -> dict:
    k = f["kpi"]
    tov = lambda x: f"{n(x)} {_plural(x, 'товар', 'товара', 'товаров')}"
    sup = lambda x: f"{n(x)} {_plural(x, 'поставщика', 'поставщиков', 'поставщиков')}"

    if k["order_now_skus"]:
        headline = (f"Сегодня нужно заказать {tov(k['order_now_skus'])} ({n(k['order_now_qty'])} шт.) "
                    f"у {sup(k['order_now_suppliers'])}.")
    elif k["order_soon_skus"]:
        headline = f"Срочных заказов нет; в ближайшие 30 дней — {tov(k['order_soon_skus'])}."
    else:
        headline = "Срочных заказов нет — запас в порядке."

    summary = []
    if k["out_of_stock_skus"]:
        summary.append(f"Нет на складе прямо сейчас: {tov(k['out_of_stock_skus'])} из тех, что продаются.")
    if k["deficit_skus"]:
        summary.append(f"{tov(k['deficit_skus']).capitalize()} закончатся раньше, чем придёт новый заказ: "
                       f"упустим около {n(k['deficit_lost_units'])} шт. продаж.")
    for p in f["season_peaks_ahead"][:2]:
        summary.append(f"{p['category']}: впереди сезон, пик — {p['peak_month']}.")
    if k["pipeline_qty"]:
        summary.append(f"В пути {n(k['pipeline_qty'])} шт. — они уже учтены, повторно их не заказываем.")
    if "changes" in f:
        c = f["changes"]
        summary.append(f"С анализа на {c['prev_as_of']}: в «Заказать сейчас» добавилось "
                       f"{tov(c['became_order_now'])}, ушло {tov(c['left_order_now'])}.")

    actions = []
    for s in f["top_suppliers"][:3]:
        actions.append(f"Отправить заказ поставщику {s['supplier_id']}: {tov(s['skus'])}, {n(s['qty'])} шт., "
                       f"приход ожидается к {s['arrival']}.")
    if k["deficit_skus"]:
        actions.append(f"По {n(k['deficit_skus'])} {_plural(k['deficit_skus'], 'товару', 'товарам', 'товарам')} "
                       "из списка «Не успеем довезти» поискать быструю поставку (авиа, местный поставщик).")
    if k["doubtful_po"]:
        actions.append(f"Уточнить у поставщиков {n(k['doubtful_po'])} "
                       f"{_plural(k['doubtful_po'], 'сильно просроченный заказ', 'сильно просроченных заказа', 'сильно просроченных заказов')} "
                       f"({n(k['doubtful_qty'])} шт.) — в расчёте они не учтены.")

    warnings = []
    if k["review_price_skus"]:
        warnings.append(f"{tov(k['review_price_skus']).capitalize()} с низкой наценкой не попали в заказ — "
                        "проверьте цену или выведите из ассортимента.")
    if k["excess_skus"]:
        warnings.append(f"Избыток по {n(k['excess_skus'])} {_plural(k['excess_skus'], 'товару', 'товарам', 'товарам')}: "
                        f"лишние около {n(k['excess_qty'])} шт.")
    if k["need_supplier_skus"]:
        warnings.append(f"{tov(k['need_supplier_skus']).capitalize()} пора заказывать, но не указан поставщик.")
    return {"headline": headline, "summary": summary[:4], "actions": actions[:4], "warnings": warnings[:3],
            "source": "rules"}


# ------------------------------------------------------------------ проверка чисел
NUM = re.compile(r"\d[\d   ]*(?:[.,]\d+)?")


def _nums_in(obj) -> set[str]:
    out = set()
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _nums_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _nums_in(v)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out.add(_norm(str(round(obj, 2) if isinstance(obj, float) else obj)))
        out.add(_norm(str(int(round(obj)))))
    elif isinstance(obj, str):
        out |= {_norm(m) for m in NUM.findall(obj)}
    return out


def _norm(s: str) -> str:
    s = re.sub(r"[   ]", "", s).replace(",", ".")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def check_numbers(text_obj: dict, f: dict) -> list[str]:
    allowed = _nums_in(f) | {str(i) for i in range(0, 13)} | {"30"}  # мелкие порядковые и «30 дней»
    found = set()
    for part in [text_obj.get("headline", "")] + sum([text_obj.get(k, []) for k in ("summary", "actions", "warnings")], []):
        # артикулы и коды поставщиков (SKU-…, SUP-…) — не числа
        part = re.sub(r"\b[A-Z]{3}-[0-9A-F]{6}\b", "", part)
        found |= {_norm(m) for m in NUM.findall(part)}
    return sorted(x for x in found if x and x not in allowed)


# ------------------------------------------------------------------ внешний сервис
def via_api(f: dict, s: Settings) -> dict | None:
    """Комментарий от сервиса, выбранного в «Управлении» (writers.py). None — сервис не выбран."""
    from . import writers
    a = writers.active(s)
    if not a:
        return None
    svc = writers.BY_ID[a["service"]]
    if svc["needs_key"] and not a["key"]:
        return {"error": f"нет ключа для {svc['title']}"}
    system = (s.root / "prompts" / "analyst.md").read_text(encoding="utf-8")
    user = "Факты расчёта:\n```json\n" + json.dumps(f, ensure_ascii=False, indent=1) + "\n```"
    if a["service"] != "claude":  # остальным напоминаем формат прямо в запросе
        user += ("\n\nОтветь только JSON-объектом с ключами headline (строка), summary, actions, warnings "
                 "(списки строк). Без пояснений до и после.")
    try:
        text = writers.chat(a, system, user, s.root)
    except writers.WriterError as e:
        return {"error": f"{svc['title']}: {e}"}
    except Exception as e:  # noqa: BLE001 — нет сети и т.п.: работаем по шаблону
        return {"error": f"{svc['title']}: не удалось получить комментарий ({type(e).__name__})"}
    try:
        out = writers.parse_json(text)
    except (ValueError, json.JSONDecodeError):
        return {"error": f"{svc['title']}: ответ не в нужном формате"}
    bad = check_numbers(out, f)
    if bad:
        return {"error": f"{svc['title']}: в тексте есть числа, которых нет в расчёте ({', '.join(bad[:5])})"}
    out["source"] = a["service"]
    return out


def make(result: dict, s: Settings, changes: dict | None = None, mode: str | None = None,
         override: dict | None = None) -> dict:
    """mode: 'авто' (API, если есть ключ), 'правила', 'api'. override — готовый комментарий (из Claude Code)."""
    f = facts(result, changes)
    mode = mode or s.commentary_mode
    if override:
        bad = check_numbers(override, f)
        if not bad:
            return override | {"source": override.get("source", "claude-code")}
        note = f"комментарий отброшен: числа не из расчёта ({', '.join(bad[:5])})"
        return rule_based(f) | {"note": note}
    if mode in ("авто", "api"):
        out = via_api(f, s)
        if out and "error" not in out:
            return out
        if out and "error" in out:
            return rule_based(f) | {"note": out["error"]}
    return rule_based(f)
