"""Загрузчик из МойСклад по API (https://api.moysklad.ru/api/remap/1.2).

Перед каждым пересчётом забирает из МойСклад товары, продажи по месяцам, остатки на 1-е число,
заказы поставщикам и приёмки и кладёт их zip-архивом «moysklad_ГГГГ-ММ-ДД.zip» в папку с выгрузками.
Дальше архив разбирает обычный приём выгрузок (sources.intake) — расчёт и дашборд не меняются.

Токен — только в .env (MOYSKLAD_TOKEN) или в секретах GitHub. В config.toml, дашборд и журнал он не попадает.
Включается в config.toml, раздел «мойсклад», или в «Управление» → «Откуда берутся данные».
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime

from .config import Settings
from .data import DataError

BASE = "https://api.moysklad.ru/api/remap/1.2"
TOKEN_ENV = "MOYSKLAD_TOKEN"
SECTION = "мойсклад"
PAGE = 1000          # строк на страницу у отчётов и справочников
PAGE_EXPAND = 100    # у документов с позициями (expand) — не больше 100
TIMEOUT = 60


class MoySkladError(DataError):
    """Понятная пользователю ошибка подключения к МойСклад."""


# ------------------------------------------------------------------ настройки
def config(s: Settings) -> dict:
    from . import settings as st
    cp = s.root / "config.toml"
    raw = (st.read_raw(cp) if cp.exists() else st.read_raw()).get(SECTION, {}) or {}
    return {"on": bool(raw.get("включено", False)),
            "months": int(raw.get("месяцев_истории", 36) or 36),
            "store": str(raw.get("склад", "") or "").strip()}


def has_token(s: Settings) -> bool:
    from . import writers
    return writers.has_key(s.root, TOKEN_ENV)


def enabled(s: Settings) -> bool:
    return config(s)["on"] and has_token(s)


def _status_path(s: Settings):
    return s.out_dir / "moysklad.json"


def status(s: Settings) -> dict:
    """Для панели «Управление»: включено ли, есть ли токен, как прошла последняя загрузка (без токена)."""
    p = _status_path(s)
    last = None
    if p.exists():
        try:
            last = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            last = None
    return {**config(s), "has_token": has_token(s), "last": last}


def _save_status(s: Settings, rec: dict) -> None:
    s.out_dir.mkdir(parents=True, exist_ok=True)
    _status_path(s).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ HTTP
def _http(url: str, token: str) -> dict:
    """Один GET к API. Заменяется в тестах."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip",
                                               "Accept": "application/json;charset=utf-8"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 — адрес фиксированный
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:  # лимит запросов — подождать и повторить
                time.sleep(float(e.headers.get("X-Lognex-Retry-After", 1000)) / 1000 + 0.5)
                continue
            if e.code in (401, 403):
                raise MoySkladError("МойСклад не принял токен. Создайте новый в МойСклад: «Настройки» → "
                                    "«Токены» и вставьте его в «Управление» → «Откуда берутся данные».") from None
            raise MoySkladError(f"МойСклад ответил ошибкой {e.code}. Попробуйте обновить позже.") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise MoySkladError("Нет связи с МойСклад. Проверьте интернет и попробуйте ещё раз.") from e
    raise MoySkladError("МойСклад не отвечает: слишком много запросов. Попробуйте через минуту.")


HTTP = _http


def _url(path: str, params: dict | None = None) -> str:
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")},
                               safe=";:=></,.", quote_via=urllib.parse.quote)
    return f"{BASE}{path}" + (f"?{q}" if q else "")


def _rows(path: str, token: str, params: dict | None = None, limit: int = PAGE):
    """Все строки списка постранично."""
    offset = 0
    while True:
        data = HTTP(_url(path, {**(params or {}), "limit": limit, "offset": offset}), token)
        rows = data.get("rows") or []
        yield from rows
        size = (data.get("meta") or {}).get("size")
        offset += len(rows)
        if not rows or len(rows) < limit or (size is not None and offset >= size):
            return


def _id(meta_holder) -> str:
    """uuid объекта из его meta.href."""
    href = ((meta_holder or {}).get("meta") or {}).get("href", "")
    return href.rstrip("/").split("/")[-1].split("?")[0]


def _moment(d: date) -> str:
    return f"{d:%Y-%m-%d} 00:00:00"


def _day(v) -> str:
    return str(v or "")[:10]


def _months_back(today: date, n: int) -> list[date]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


# ------------------------------------------------------------------ сбор данных
def test(token: str) -> str:
    """Проверить токен: вернуть название организации."""
    data = HTTP(_url("/entity/organization", {"limit": 1}), token)
    rows = data.get("rows") or []
    return rows[0].get("name", "организация") if rows else "организация"


def fetch(token: str, today: date, months: int = 36, store: str = "") -> dict[str, list[dict]]:
    """Забрать данные и привести к таблицам, которые понимает приём выгрузок."""
    # поставщики: id контрагента → код или название
    agents = {}
    for c in _rows("/entity/counterparty", token):
        agents[_id(c)] = (c.get("code") or c.get("name") or "").strip()

    # товары: id → артикул; категория — путь папки; наценка — по ценам карточки (уточняется продажами ниже)
    goods, sku = {}, {}
    for p in _rows("/entity/product", token):
        sid = (p.get("article") or p.get("code") or p.get("name") or "").strip()
        if not sid:
            continue
        goods[_id(p)] = sid
        buy = ((p.get("buyPrice") or {}).get("value") or 0) / 100
        sale = next((x.get("value", 0) / 100 for x in (p.get("salePrices") or []) if x.get("value")), 0)
        sku[sid] = {"sku_id": sid, "category": (p.get("pathName") or "").split("/")[-1] or "без категории",
                    "supplier_id": agents.get(_id(p.get("supplier")), "") if p.get("supplier") else "",
                    "avg_markup_pct_12m": round((sale - buy) / buy * 100, 1) if buy > 0 and sale > 0 else ""}

    def sku_of(row) -> str:
        a = row.get("assortment") or row
        uid = _id(a)
        if uid in goods:
            return goods[uid]
        sid = (a.get("article") or a.get("code") or a.get("name") or "").strip()
        if sid and sid not in sku:  # модификация или товар, которого нет в справочнике
            sku[sid] = {"sku_id": sid, "category": "без категории", "supplier_id": "", "avg_markup_pct_12m": ""}
        return sid

    store_f = {"filter": f"store={BASE}/entity/store/{store}"} if store else {}
    first_days = _months_back(today, months)

    # продажи по месяцам: отчёт «Прибыльность по товарам»; наценка за последние 12 месяцев — по выручке и себестоимости
    sales, money = [], {}
    for i, m in enumerate(first_days):
        end = min(_next_month(m), today)
        params = {"momentFrom": _moment(m), "momentTo": f"{end:%Y-%m-%d} 00:00:00" if end > m else _moment(m), **store_f}
        for r in _rows("/report/profit/byproduct", token, params):
            sid = sku_of(r)
            if not sid:
                continue
            sales.append({"sku_id": sid, "month": f"{m:%Y-%m}", "qty_sold": r.get("sellQuantity", 0) or 0,
                          "qty_returned": r.get("returnQuantity", 0) or 0})
            if i >= len(first_days) - 12:
                acc = money.setdefault(sid, [0.0, 0.0])
                acc[0] += (r.get("sellSum") or 0) / 100
                acc[1] += (r.get("sellCostSum") or 0) / 100
    for sid, (rev, cost) in money.items():
        if cost > 0 and rev > 0 and sid in sku:
            sku[sid]["avg_markup_pct_12m"] = round((rev - cost) / cost * 100, 1)

    # остатки на 1-е число каждого месяца
    stock = []
    for m in first_days:
        for r in _rows("/report/stock/all", token, {"moment": _moment(m), **store_f}):
            sid = sku_of(r)
            if sid:
                stock.append({"sku_id": sid, "month": f"{m:%Y-%m}", "stock_qty": max(r.get("stock", 0) or 0, 0)})

    since = _moment(first_days[0])
    # заказы поставщикам: проведённые, за период истории
    po = []
    for d in _rows("/entity/purchaseorder", token, {"filter": f"moment>={since};applicable=true",
                                                    "expand": "positions.assortment"}, PAGE_EXPAND):
        sup = agents.get(_id(d.get("agent")), "")
        planned = _day(d.get("deliveryPlannedMoment"))
        ordered = _day(d.get("moment"))
        positions = (d.get("positions") or {}).get("rows") or []
        for ps in positions:
            sid = sku_of(ps)
            if not sid:
                continue
            q, got = ps.get("quantity", 0) or 0, ps.get("shipped", 0) or 0
            lead = (date.fromisoformat(planned) - date.fromisoformat(ordered)).days if planned and ordered else ""
            po.append({"po_id": d.get("name") or _id(d), "sku_id": sid, "supplier_id": sup, "order_date": ordered,
                       "promised_date": planned or ordered, "qty_ordered": q, "qty_received": min(got, q),
                       "qty_pending": max(q - got, 0), "status": "closed" if got >= q else "open",
                       "planned_lead_days": lead})

    # приёмки
    receipts = []
    for d in _rows("/entity/supply", token, {"filter": f"moment>={since};applicable=true",
                                             "expand": "positions.assortment"}, PAGE_EXPAND):
        sup = agents.get(_id(d.get("agent")), "")
        for ps in (d.get("positions") or {}).get("rows") or []:
            sid = sku_of(ps)
            if sid:
                receipts.append({"sku_id": sid, "receipt_date": _day(d.get("moment")), "qty": ps.get("quantity", 0) or 0,
                                 "supplier_id": sup})

    return {"sku_dim.csv": list(sku.values()), "sales_monthly.csv": sales, "stock_monthly.csv": stock,
            "purchase_orders.csv": po, "receipts.csv": receipts}


def _csv(rows: list[dict], cols: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


COLUMNS = {
    "sku_dim.csv": ["sku_id", "category", "supplier_id", "avg_markup_pct_12m"],
    "sales_monthly.csv": ["sku_id", "month", "qty_sold", "qty_returned"],
    "stock_monthly.csv": ["sku_id", "month", "stock_qty"],
    "purchase_orders.csv": ["po_id", "sku_id", "supplier_id", "order_date", "promised_date", "qty_ordered",
                            "qty_received", "qty_pending", "status", "planned_lead_days"],
    "receipts.csv": ["sku_id", "receipt_date", "qty", "supplier_id"],
}


def pull(s: Settings, today: date | None = None) -> str | None:
    """Забрать данные из МойСклад в папку с выгрузками. None — загрузчик выключен."""
    if not config(s)["on"]:
        return None
    token = os.environ.get(TOKEN_ENV, "") if has_token(s) else ""
    if not token:
        raise MoySkladError("МойСклад включён, но токен не задан. Вставьте его в «Управление» → «Откуда берутся данные».")
    from .sources import source_folder
    today = today or date.today()
    cfg = config(s)
    t0 = time.time()
    rec = {"when": datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        tables = fetch(token, today, cfg["months"], cfg["store"])
        if not tables["sku_dim.csv"] or not tables["stock_monthly.csv"]:
            raise MoySkladError("МойСклад вернул пустой список товаров или остатков. Проверьте, что у токена есть доступ "
                                "к товарам и отчётам, и что выбран правильный склад.")
        folder, custom = source_folder(s)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"moysklad_{today:%Y-%m-%d}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, rows in tables.items():
                z.writestr(name, _csv(rows, COLUMNS[name]))
    except MoySkladError as e:
        _save_status(s, {**rec, "ok": False, "error": str(e), "seconds": round(time.time() - t0)})
        raise
    _save_status(s, {**rec, "ok": True, "seconds": round(time.time() - t0), "file": path.name,
                     "rows": {k.removesuffix(".csv"): len(v) for k, v in tables.items()}})
    return path.name
