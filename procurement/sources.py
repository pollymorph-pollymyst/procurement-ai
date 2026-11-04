"""Приём выгрузок из любой учётной системы: 1С (УТ, ERP, КА), МойСклад, Excel.

Что делает:
1. Берёт новые файлы из папки с выгрузками: zip, xlsx, csv (UTF-8 или Windows-1251, разделитель любой).
2. В каждом файле (и каждом листе Excel) находит строку заголовков — отчёты 1С часто начинаются с шапки.
3. Определяет, что это за данные (продажи, остатки, заказы…), и сопоставляет колонки по названиям.
   Если не получилось — файл попадает в отчёт «не распознан», и в панели «Управление» колонки
   один раз сопоставляются вручную (mapping.json); дальше такие файлы распознаются сами.
4. Достраивает то, чего нет в выгрузке, но можно вычислить (поставщики — из поступлений, срок
   поставки — из дат заказа, месяц — из даты документа…), и пишет таблицы в data/raw в едином виде.
5. Сохраняет отчёт outputs/intake.json: что пришло, сколько строк, за какой период, что достроено.

Таблицы, которых нет в новой выгрузке, берутся из прошлой (например, справочник товаров
обычно выгружают реже, чем продажи).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import Settings
from .data import DataError


class IntakeError(DataError):
    """Понятная пользователю ошибка выгрузки (наследники DataError ловятся так же)."""


def norm(s) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", str(s).lower().replace("ё", "е"))


def _syn(*words):
    return [norm(w) for w in words]


SKU = _syn("sku_id", "sku", "артикул", "кодтовара", "код номенклатуры", "номенклатура код", "код", "номенклатура артикул",
           "номенклатура", "товар", "наименование", "productid", "product")
SUP = _syn("supplier_id", "supplier", "поставщик", "основной поставщик", "контрагент", "поставщик код", "vendor")
TYPE = _syn("supply_type", "тип поставки", "тип поставщика", "импорт", "происхождение")
MONTH = _syn("month", "месяц", "период", "дата", "период месяц", "дата документа", "date")

# key, файл, название, колонки [(колонка, название, обязательна, синонимы)], подсказки в имени файла
TABLES = {
    "sku": dict(file="sku_dim.csv", title="Товары (справочник номенклатуры)",
                hints=["номенклат", "товар", "справочник", "sku", "product", "item"],
                cols=[("sku_id", "Артикул / код товара", True, SKU),
                      ("category", "Категория (группа товаров)", True,
                       _syn("category", "категория", "группа", "группа товаров", "товарная группа", "вид номенклатуры", "группа номенклатуры", "родитель")),
                      ("supplier_id", "Основной поставщик", False, SUP),
                      ("supply_type", "Импорт или местный", False, TYPE),
                      ("avg_markup_pct_12m", "Наценка, %", False,
                       _syn("avg_markup_pct_12m", "markup", "наценка", "наценка %", "средняя наценка", "наценка процент", "рентабельность")),
                      ("price", "Цена продажи", False, _syn("price", "цена", "цена продажи", "розничная цена")),
                      ("cost", "Себестоимость / закупочная цена", False, _syn("cost", "себестоимость", "закупочная цена", "цена закупки"))]),
    "sales": dict(file="sales_monthly.csv", title="Продажи",
                  hints=["продаж", "реализац", "sales", "отгруз", "выручк"],
                  cols=[("sku_id", "Артикул / код товара", True, SKU),
                        ("month", "Месяц или дата продажи", True, MONTH),
                        ("qty_sold", "Продано, шт.", True,
                         _syn("qty_sold", "продано", "продажи", "продано шт", "количество продано", "расход", "отгружено", "реализовано", "количество", "qty")),
                        ("qty_returned", "Возвраты, шт.", False, _syn("qty_returned", "возврат", "возвраты", "возвращено")),
                        ("category", "Категория", False, _syn("category", "категория", "группа", "группа товаров")),
                        ("partial_month", "Месяц неполный (1/0)", False, _syn("partial_month", "неполный месяц"))]),
    "stock": dict(file="stock_monthly.csv", title="Остатки на складе",
                  hints=["остат", "stock", "склад", "наличи"],
                  cols=[("sku_id", "Артикул / код товара", True, SKU),
                        ("month", "Месяц или дата остатка", True, MONTH + _syn("на начало", "на дату")),
                        ("stock_qty", "Остаток, шт.", True,
                         _syn("stock_qty", "остаток", "остатки", "на складе", "начальный остаток", "конечный остаток", "в наличии", "наличие", "количество", "qty"))]),
    "po": dict(file="purchase_orders.csv", title="Заказы поставщикам",
               hints=["заказ", "order", "po", "закупк"],
               cols=[("po_id", "Номер заказа", True, _syn("po_id", "номер", "номер заказа", "заказ", "заказ поставщику", "документ", "order", "orderid")),
                     ("sku_id", "Артикул / код товара", True, SKU),
                     ("supplier_id", "Поставщик", False, SUP),
                     ("order_date", "Дата заказа", True, _syn("order_date", "дата заказа", "дата", "дата документа", "orderdate")),
                     ("promised_date", "Ожидаемая дата прихода", True,
                      _syn("promised_date", "дата поступления", "ожидаемая дата", "дата поставки", "плановая дата", "дата прихода", "срок поставки дата", "ожидается")),
                     ("qty_ordered", "Заказано, шт.", True, _syn("qty_ordered", "заказано", "количество заказано", "количество", "qty")),
                     ("qty_received", "Уже пришло, шт.", False, _syn("qty_received", "поступило", "получено", "принято", "пришло", "оприходовано")),
                     ("qty_pending", "Ещё в пути, шт.", False, _syn("qty_pending", "осталось", "в пути", "к поступлению", "осталось поставить", "ожидается шт")),
                     ("status", "Состояние заказа", False, _syn("status", "статус", "состояние")),
                     ("planned_lead_days", "Плановый срок, дн.", False, _syn("planned_lead_days", "срок поставки", "срок дней", "срок поставки дн")),
                     ("qty_confirmed", "Подтверждено поставщиком, шт.", False, _syn("qty_confirmed", "подтверждено")),
                     ("supply_type", "Импорт или местный", False, TYPE)]),
    "receipts": dict(file="receipts.csv", title="Поступления товаров",
                     hints=["поступлен", "приход", "receipt", "приемк", "приёмк"],
                     cols=[("sku_id", "Артикул / код товара", True, SKU),
                           ("receipt_date", "Дата поступления", True, _syn("receipt_date", "дата поступления", "дата", "дата прихода", "дата документа")),
                           ("qty", "Пришло, шт.", True, _syn("qty", "количество", "поступило", "приход", "принято", "оприходовано")),
                           ("supplier_id", "Поставщик", False, SUP)]),
    "suppliers": dict(file="supplier_dim.csv", title="Поставщики",
                      hints=["поставщик", "контрагент", "supplier", "vendor"],
                      cols=[("supplier_id", "Поставщик", True, SUP),
                            ("supply_type", "Импорт или местный", False, TYPE),
                            ("median_planned_lead_days", "Срок поставки, дн.", False, _syn("median_planned_lead_days", "срок поставки", "срок", "дней", "lead time")),
                            ("median_days_between_receipts", "Заказ раз в, дн.", False, _syn("median_days_between_receipts", "цикл заказа", "периодичность"))]),
}
REQUIRED_TABLES = ["sku", "sales", "stock"]
ALL_SYN = {w for t in TABLES.values() for c in t["cols"] for w in c[3]}


def schema_for_panel() -> list[dict]:
    return [{"key": k, "title": t["title"], "cols": [{"key": c[0], "title": c[1], "required": c[2]} for c in t["cols"]]}
            for k, t in TABLES.items()]


# ------------------------------------------------------------------ настройки источника и сопоставления

def mapping_path(s: Settings) -> Path:
    return s.root / "mapping.json"


def read_mapping(s: Settings) -> dict:
    p = mapping_path(s)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def save_mapping(s: Settings, sig: str, table: str, columns: dict) -> None:
    if table != "_skip" and table not in TABLES:
        raise IntakeError("Неизвестный вид данных.")
    if table != "_skip":
        allowed = {c[0] for c in TABLES[table]["cols"]}
        bad = [v for v in columns.values() if v not in allowed]
        if bad:
            raise IntakeError("Неизвестная колонка: " + ", ".join(bad))
        need = [c[1] for c in TABLES[table]["cols"] if c[2] and c[0] not in columns.values()]
        if need:
            raise IntakeError("Не указаны обязательные колонки: " + ", ".join(need) + ".")
    m = read_mapping(s)
    m[sig] = {"table": table, "columns": columns, "saved": datetime.now().isoformat(timespec="seconds")}
    mapping_path(s).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    # файл, который не распознали, снова считается новым — его возьмёт следующий пересчёт
    seen = _read_seen(s)
    seen["files"] = {k: v for k, v in seen.get("files", {}).items() if v.get("sig") != sig}
    _write_seen(s, seen)


def source_folder(s: Settings) -> tuple[Path, bool]:
    from . import settings as st
    raw = st.read_raw().get("источник", {})
    folder = str(raw.get("папка", "") or "").strip()
    if not folder:
        return s.inbox_dir, False
    p = Path(folder)
    return (p if p.is_absolute() else s.root / p), True


# ------------------------------------------------------------------ чтение файлов

def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:  # архиватор Windows пишет кириллицу в cp866
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _decode(b: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1")


def _read_csv(b: bytes) -> pd.DataFrame:
    text = _decode(b)
    sample = text[:20000]
    try:
        sep = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        sep = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=sep))  # строки разной длины: шапка отчёта, «Итого»
    width = max((len(r) for r in rows), default=0)
    return pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)


def _frames(name: str, b: bytes):
    """(имя, лист, DataFrame без заголовков) для каждого листа/файла."""
    low = name.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                inner = Path(_zip_name(info)).name
                if inner.lower().endswith((".csv", ".xlsx", ".xlsm", ".txt", ".xls")):
                    yield from _frames(inner, z.read(info))
        return
    if low.endswith((".xlsx", ".xlsm")):
        for sheet, df in pd.read_excel(io.BytesIO(b), sheet_name=None, header=None, dtype=str).items():
            yield name, sheet, df.fillna("")
        return
    if low.endswith(".xls"):
        raise IntakeError(f"Файл «{name}» в старом формате Excel (.xls). Сохраните его как .xlsx или .csv.")
    yield name, None, _read_csv(b)


def _find_header(df: pd.DataFrame) -> int | None:
    best, best_i = 0, None
    for i in range(min(len(df), 30)):
        cells = [norm(v) for v in df.iloc[i].tolist() if str(v).strip()]
        score = sum(1 for c in cells if c in ALL_SYN)
        if score > best:
            best, best_i = score, i
    return best_i if best >= 2 else (0 if len(df) else None)


def _table_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    h = _find_header(df)
    if h is None:
        return pd.DataFrame(), []
    headers, seen = [], {}
    for v in df.iloc[h].tolist():
        v = str(v).strip() or "колонка"
        seen[v] = seen.get(v, 0) + 1
        headers.append(v if seen[v] == 1 else f"{v} ({seen[v]})")
    body = df.iloc[h + 1:].copy()
    body.columns = headers
    body = body.loc[:, [c for c in headers if not c.startswith("колонка") or body[c].astype(str).str.strip().ne("").any()]]
    first = body.iloc[:, 0].astype(str).str.strip().str.lower() if len(body.columns) else pd.Series(dtype=str)
    body = body[~first.str.startswith(("итого", "всего", "total"))]
    body = body[body.astype(str).apply(lambda r: r.str.strip().ne("").any(), axis=1)]
    return body.reset_index(drop=True), list(body.columns)


def signature(headers: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(norm(h) for h in headers)).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ распознавание

def _match_cols(table: str, headers: list[str]) -> dict:
    """{заголовок: колонка}: сначала точное совпадение, потом «содержит»."""
    nh = {h: norm(h) for h in headers}
    used, out = set(), {}
    cols = sorted(TABLES[table]["cols"], key=lambda c: not c[2])  # обязательные первыми
    for exact in (True, False):
        for key, _, _, syns in cols:
            if key in out.values():
                continue
            for w in syns:  # синонимы по порядку важности
                hit = next((h for h, n in nh.items() if h not in used and (n == w if exact else (len(w) >= 4 and w in n))), None)
                if hit:
                    out[hit] = key
                    used.add(hit)
                    break
    return out


def _guess(name: str, sheet: str | None, headers: list[str]) -> tuple[str | None, dict, float]:
    label = norm(f"{name} {sheet or ''}")
    best = (None, {}, 0.0)
    for key, t in TABLES.items():
        cols = _match_cols(key, headers)
        req = [c[0] for c in t["cols"] if c[2]]
        got = sum(1 for c in req if c in cols.values())
        score = got / len(req) + 0.1 * sum(1 for c in t["cols"] if not c[2] and c[0] in cols.values())
        if any(norm(hint) in label for hint in t["hints"]):
            score += 0.6
        if got == len(req) and score > best[2]:
            best = (key, cols, score)
        elif best[0] is None and score > best[2]:
            best = (key, cols, score * 0.5)
    return best


# ------------------------------------------------------------------ приведение к единому виду

def _num(sr: pd.Series) -> pd.Series:
    s = sr.astype(str).str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
    s = s.str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


RU_MONTHS = {m: i + 1 for i, m in enumerate(["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"])}


def _dates(sr: pd.Series) -> pd.Series:
    s = sr.astype(str).str.strip()
    iso = s.str.match(r"^\d{4}-\d{2}")
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    out[iso] = pd.to_datetime(s[iso].str[:10].where(s[iso].str.len() >= 10, s[iso].str[:7] + "-01"), errors="coerce")
    rest = ~iso
    out[rest] = pd.to_datetime(s[rest].str.split(" ").str[0], format="%d.%m.%Y", errors="coerce")
    left = out.isna() & s.ne("")
    if left.any():  # «Август 2026», «08.2026»
        def ru(v):
            m = re.match(r"([а-яё]+)\s+(\d{4})", v.lower())
            if m:
                k = next((n for w, n in RU_MONTHS.items() if m.group(1).startswith(w)), None)
                return pd.Timestamp(int(m.group(2)), k, 1) if k else pd.NaT
            m = re.match(r"(\d{1,2})[./](\d{4})$", v)
            return pd.Timestamp(int(m.group(2)), int(m.group(1)), 1) if m else pd.NaT
        out[left] = s[left].map(ru)
    return out


def _supply_type(sr: pd.Series) -> pd.Series:
    v = sr.astype(str).str.lower().str.strip()
    out = pd.Series("unknown", index=sr.index)
    out[v.str.contains("импорт|import|китай|china|внешн|зарубеж|ввоз|asia|азия")] = "import"
    out[v.str.contains("местн|local|росси|внутрен|отечеств")] = "local"
    return out


def _status(sr: pd.Series) -> pd.Series:
    v = sr.astype(str).str.lower().str.strip()
    out = pd.Series("", index=sr.index)
    out[v.str.contains("закрыт|выполнен|исполнен|completed|closed|отмен|cancel")] = "closed"
    out[v.str.contains("completed|выполнен|исполнен")] = "completed"
    out[v.str.contains("open|открыт|в работе|подтвержд|ожида|к поступлению|согласован|в пути|отгружен")] = "open"
    return out


def _normalize(key: str, df: pd.DataFrame, cols: dict, notes: list[str]) -> pd.DataFrame:
    out = pd.DataFrame({c: df[h] for h, c in cols.items()})
    if "sku_id" in out:
        out["sku_id"] = out["sku_id"].astype(str).str.strip()
        out = out[out["sku_id"].ne("") & out["sku_id"].ne("nan")]
    if key == "sku":
        out["category"] = out["category"].astype(str).str.strip().replace("", "без категории")
        if "avg_markup_pct_12m" in out:
            out["avg_markup_pct_12m"] = _num(out["avg_markup_pct_12m"])
        elif "price" in out and "cost" in out:
            p, c = _num(out["price"]), _num(out["cost"])
            out["avg_markup_pct_12m"] = ((p - c) / c * 100).where(c > 0).round(1)
            notes.append("наценка посчитана из цены и себестоимости")
        else:
            out["avg_markup_pct_12m"] = pd.NA
            notes.append("наценки нет — отсечение по наценке не работает")
        if "supply_type" in out:
            out["supply_type"] = _supply_type(out["supply_type"])
        out = out.drop(columns=[c for c in ("price", "cost") if c in out]).drop_duplicates("sku_id", keep="last")
    elif key in ("sales", "stock"):
        d = _dates(out["month"])
        bad = d.isna().sum()
        if bad:
            notes.append(f"{bad} строк без понятной даты пропущено")
        out = out[d.notna()].copy()
        out["month"] = d[d.notna()].dt.strftime("%Y-%m")
        q = "qty_sold" if key == "sales" else "stock_qty"
        out[q] = _num(out[q]).fillna(0)
        if key == "sales":
            out["qty_returned"] = _num(out["qty_returned"]).fillna(0) if "qty_returned" in out else 0
            agg = {"qty_sold": "sum", "qty_returned": "sum"}
            if "partial_month" in out:
                out["partial_month"] = _num(out["partial_month"]).fillna(0).astype(int)
                agg["partial_month"] = "max"
            if "category" in out:
                agg["category"] = "last"
            out = out.groupby(["sku_id", "month"], as_index=False).agg(agg)
        else:
            # несколько дат в месяце — берём самую раннюю (остаток на начало месяца)
            out["_d"] = d[d.notna()]
            out = out.sort_values("_d").groupby(["sku_id", "month"], as_index=False).first().drop(columns="_d")
    elif key == "po":
        for c in ("order_date", "promised_date"):
            out[c] = _dates(out[c])
        out = out[out["order_date"].notna()]
        out["promised_date"] = out["promised_date"].fillna(out["order_date"] + pd.to_timedelta(120, unit="D"))
        out["qty_ordered"] = _num(out["qty_ordered"]).fillna(0)
        out["qty_received"] = _num(out["qty_received"]).fillna(0) if "qty_received" in out else 0
        if "qty_pending" in out:
            out["qty_pending"] = _num(out["qty_pending"]).fillna(0)
        else:
            out["qty_pending"] = (out["qty_ordered"] - out["qty_received"]).clip(lower=0)
            notes.append("«ещё в пути» = заказано − пришло")
        st = _status(out["status"]) if "status" in out else pd.Series("", index=out.index)
        st[st.eq("")] = out["qty_pending"].gt(0).map({True: "open", False: "closed"})
        out["status"] = st
        lead = (out["promised_date"] - out["order_date"]).dt.days
        out["planned_lead_days"] = _num(out["planned_lead_days"]).fillna(lead) if "planned_lead_days" in out else lead
        out["qty_confirmed"] = _num(out["qty_confirmed"]).fillna(0) if "qty_confirmed" in out else 0
        if "supply_type" in out:
            out["supply_type"] = _supply_type(out["supply_type"])
        for c in ("order_date", "promised_date"):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    elif key == "receipts":
        d = _dates(out["receipt_date"])
        out = out[d.notna()].copy()
        out["receipt_date"] = d[d.notna()].dt.strftime("%Y-%m-%d")
        out["qty"] = _num(out["qty"]).fillna(0)
        out = out[out["qty"] > 0]
    elif key == "suppliers":
        out["supplier_id"] = out["supplier_id"].astype(str).str.strip()
        if "supply_type" in out:
            out["supply_type"] = _supply_type(out["supply_type"])
        for c in ("median_planned_lead_days", "median_days_between_receipts"):
            if c in out:
                out[c] = _num(out[c])
        out = out.drop_duplicates("supplier_id", keep="last")
    for c in ("supplier_id",):
        if c in out:
            out[c] = out[c].astype(str).str.strip().replace({"nan": ""})
    return out.reset_index(drop=True)


def _complete(t: dict, export_date: date, notes: dict) -> dict:
    """Достроить недостающие колонки и таблицы из того, что есть."""
    sku, sales = t["sku"], t["sales"]
    cat = sku.set_index("sku_id")["category"]
    if "category" not in sales or sales["category"].astype(str).eq("").all():
        sales["category"] = sales["sku_id"].map(cat)
    sales["category"] = sales["category"].fillna(sales["sku_id"].map(cat)).fillna("без категории")
    # пропущенные месяцы без продаж = 0 продаж (календарь плотный, как ждёт прогноз)
    last = sales["month"].max()
    full = []
    for sid, g in sales.groupby("sku_id"):
        months = pd.period_range(g["month"].min(), last, freq="M").strftime("%Y-%m")
        if len(months) != len(g):
            g = g.set_index("month").reindex(months).rename_axis("month").reset_index()
            g["sku_id"] = sid
            g[["qty_sold", "qty_returned"]] = g[["qty_sold", "qty_returned"]].fillna(0)
            g["category"] = g["category"].ffill().bfill()
        full.append(g)
    sales = pd.concat(full, ignore_index=True)
    if "partial_month" in sales and pd.to_numeric(sales["partial_month"], errors="coerce").fillna(0).gt(0).any():
        sales["partial_month"] = pd.to_numeric(sales["partial_month"], errors="coerce").fillna(0).astype(int)
    else:  # неполный месяц — тот, в котором сделана выгрузка
        ld = pd.Period(last, freq="M")
        partial = export_date.year == ld.year and export_date.month == ld.month and export_date.day < ld.days_in_month
        sales["partial_month"] = ((sales["month"] == last) & partial).astype(int)
    t["sales"] = sales

    po = t.get("po")
    if po is None:
        po = pd.DataFrame(columns=["po_id", "sku_id", "supplier_id", "order_date", "promised_date", "planned_lead_days",
                                   "status", "qty_ordered", "qty_confirmed", "qty_received", "qty_pending"])
        notes.setdefault("po", []).append("заказов в пути нет в выгрузке — считаем, что ничего не едет")
    rc = t.get("receipts")
    if rc is None:
        rc = pd.DataFrame(columns=["sku_id", "receipt_date", "qty", "supplier_id"])
        notes.setdefault("receipts", []).append("поступлений нет — сроки поставки берутся из заказов или по умолчанию")
    for df in (rc, po):
        if "supplier_id" not in df:
            df["supplier_id"] = ""
        df["supplier_id"] = df["supplier_id"].fillna("").astype(str)

    # основной поставщик товара: из справочника, иначе самый частый в поступлениях/заказах
    if "supplier_id" not in sku or sku["supplier_id"].eq("").all():
        src = pd.concat([rc[["sku_id", "supplier_id"]], po[["sku_id", "supplier_id"]]]) if len(rc) or len(po) else None
        main = src[src["supplier_id"].astype(str).ne("")].groupby("sku_id")["supplier_id"].agg(lambda x: x.value_counts().index[0]) if src is not None and len(src) else pd.Series(dtype=str)
        sku["supplier_id"] = sku["sku_id"].map(main).fillna("")
        notes.setdefault("sku", []).append("поставщик товара взят из поступлений и заказов")
    for df in (rc, po):
        if "supplier_id" not in df or df["supplier_id"].astype(str).eq("").all():
            df["supplier_id"] = df["sku_id"].map(sku.set_index("sku_id")["supplier_id"]).fillna("")

    sup = t.get("suppliers")
    ids = sorted(set(sku["supplier_id"]) | set(rc["supplier_id"].astype(str)) | set(po["supplier_id"].astype(str)) - {""})
    if sup is None:
        sup = pd.DataFrame({"supplier_id": [i for i in ids if i]})
        notes.setdefault("suppliers", []).append("список поставщиков составлен по товарам, заказам и поступлениям")
    else:
        sup = pd.concat([sup, pd.DataFrame({"supplier_id": [i for i in ids if i and i not in set(sup["supplier_id"])]})], ignore_index=True)
    if "median_planned_lead_days" not in sup or sup["median_planned_lead_days"].isna().all():
        lead = po.assign(l=pd.to_numeric(po["planned_lead_days"], errors="coerce")).groupby("supplier_id")["l"].median() if len(po) else pd.Series(dtype=float)
        sup["median_planned_lead_days"] = sup["supplier_id"].map(lead)
    if "median_days_between_receipts" not in sup or sup["median_days_between_receipts"].isna().all():
        if len(rc):
            d = rc.assign(d=pd.to_datetime(rc["receipt_date"])).drop_duplicates(["supplier_id", "d"]).sort_values("d")
            gaps = d.groupby("supplier_id")["d"].apply(lambda x: x.diff().dt.days.median())
        else:
            gaps = pd.Series(dtype=float)
        sup["median_days_between_receipts"] = sup["supplier_id"].map(gaps)
    if "supply_type" not in sup or sup["supply_type"].isna().all():
        st = sku[sku.get("supply_type", pd.Series("unknown", index=sku.index)).ne("unknown")].groupby("supplier_id")["supply_type"].first() if "supply_type" in sku else pd.Series(dtype=str)
        sup["supply_type"] = sup["supplier_id"].map(st).fillna("import")
    if "supply_type" not in sku:
        sku["supply_type"] = sku["supplier_id"].map(sup.set_index("supplier_id")["supply_type"]).fillna("unknown")
        sku.loc[sku["supplier_id"].eq(""), "supply_type"] = "unknown"
    for df in (po, rc):
        if "supply_type" not in df:
            df["supply_type"] = df["supplier_id"].map(sup.set_index("supplier_id")["supply_type"]).fillna("unknown")
    t.update(sku=sku, po=po, receipts=rc, suppliers=sup)
    return t


# ------------------------------------------------------------------ главный вход

def _read_seen(s: Settings) -> dict:
    p = s.out_dir / "intake_seen.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def _write_seen(s: Settings, data: dict) -> None:
    s.out_dir.mkdir(parents=True, exist_ok=True)
    (s.out_dir / "intake_seen.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _new_files(folder: Path, seen: dict) -> list[Path]:
    if not folder.exists():
        raise IntakeError(f"Папка с выгрузками не найдена: {folder}. Проверьте путь в «Управление» → «Откуда берутся данные».")
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".zip", ".csv", ".xlsx", ".xlsm", ".xls")
             and not p.name.startswith(("~$", "."))]
    done = seen.get("files", {})
    return sorted([p for p in files if done.get(p.name, {}).get("stamp") != f"{p.stat().st_size}:{int(p.stat().st_mtime)}"],
                  key=lambda p: p.stat().st_mtime)


def _report_path(s: Settings) -> Path:
    return s.out_dir / "intake.json"


def read_report(s: Settings) -> dict | None:
    p = _report_path(s)
    try:
        rep = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except json.JSONDecodeError:
        return None
    if rep and rep.get("unknown"):
        # файлы убрали из папки — напоминать про них незачем
        folder = Path(rep.get("folder") or source_folder(s)[0])
        if not any((folder / f).exists() for f in rep.get("files", [])):
            rep["unknown"] = []
    return rep


def _period(key, df):
    col = {"sales": "month", "stock": "month", "po": "order_date", "receipts": "receipt_date"}.get(key)
    if not col or not len(df) or col not in df:
        return ""
    lo, hi = str(df[col].min())[:7], str(df[col].max())[:7]
    f = lambda v: f"{['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'][int(v[5:7]) - 1]} {v[:4]}"
    return f"{f(lo)} – {f(hi)}" if lo != hi else f(lo)


def _scan(files: list[Path], mapping: dict) -> tuple[dict, list, dict]:
    """Прочитать файлы и разложить по таблицам. (found, unknown, notes); found[key] = [(имя, df), …]."""
    found: dict[str, list] = {}
    unknown, notes = [], {}
    for p in files:
        try:
            frames = list(_frames(p.name, p.read_bytes()))
        except IntakeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise IntakeError(f"Не получается прочитать «{p.name}»: {e}. Проверьте, что файл не повреждён и закрыт в Excel.") from e
        for name, sheet, raw in frames:
            df, headers = _table_frame(raw)
            if not headers or df.empty:
                continue
            sig = signature(headers)
            if sig in mapping:
                m = mapping[sig]
                if m["table"] == "_skip":
                    continue
                key, cols = m["table"], {h: c for h, c in m["columns"].items() if h in headers}
            else:
                key, cols, score = _guess(name, sheet, headers)
                req = [c[0] for c in TABLES[key]["cols"] if c[2]] if key else []
                if not key or any(c not in cols.values() for c in req):
                    miss = [c[1] for c in TABLES[key]["cols"] if c[2] and c[0] not in cols.values()] if key else []
                    unknown.append({"file": name, "sheet": sheet, "sig": sig, "headers": headers[:40],
                                    "samples": [str(df.iloc[0][h])[:40] if len(df) else "" for h in headers[:40]],
                                    "guess": key, "guess_cols": cols,
                                    "reason": (f"Похоже на «{TABLES[key]['title']}», но не нашлись колонки: {', '.join(miss)}." if key and miss
                                               else "Не понятно, что это за данные.")})
                    continue
            n = []
            found.setdefault(key, []).append((name if not sheet or len(frames) == 1 else f"{name} · {sheet}", _normalize(key, df, cols, n)))
            notes.setdefault(key, []).extend(n)

    return found, unknown, notes


def intake(s: Settings) -> str | None:
    """Взять новые выгрузки из папки. Возвращает описание взятого или None, если нового нет."""
    folder, custom = source_folder(s)
    folder.mkdir(exist_ok=True) if not custom else None
    seen = _read_seen(s)
    files = _new_files(folder, seen)
    if not files:
        return None
    found, unknown, notes = _scan(files, read_mapping(s))
    export_date, date_src = _export_date(files, found)

    report = {"when": datetime.now().astimezone().isoformat(timespec="seconds"), "folder": str(folder),
              "files": [p.name for p in files], "unknown": unknown, "tables": [], "export_date": export_date.isoformat(),
              "export_date_src": date_src}
    tables, src, old = {}, {}, {}
    for key in TABLES:
        old[key] = _old(s, key)
        if key in found:
            tables[key] = pd.concat([d for _, d in found[key]], ignore_index=True)
            src[key] = ", ".join(n for n, _ in found[key])
        elif old[key] is not None:
            tables[key] = old[key]
            src[key] = "из прошлой выгрузки"
    missing = [TABLES[k]["title"] for k in REQUIRED_TABLES if k not in tables]
    if missing:
        report["error"] = ("Не хватает данных: " + ", ".join(missing) + ". "
                           + ("Один или несколько файлов не распознаны — укажите, что в них, в «Управление» → «Откуда берутся данные»."
                              if unknown else "Добавьте их в выгрузку."))
        report["tables"] = [{"key": k, "title": t["title"], "ok": k in tables, "file": src.get(k), "rows": len(tables[k]) if k in tables else None,
                             "notes": notes.get(k, [])} for k, t in TABLES.items()]
        _save_report(s, report)
        raise IntakeError(report["error"])
    # неполная выгрузка (один склад, одна группа, чужие артикулы) молча испортила бы план — останавливаемся
    problems = _check_complete(tables, old, found)
    stamps = sorted(_stamp(p) for p in files)
    if problems and not set(stamps) <= _accepted(s):
        report["error"] = ("Похоже, выгрузка неполная — расчёт не обновлён, на странице прошлые данные. "
                           + " ".join(problems)
                           + " Проверьте, что выгружены все склады и все группы товаров, и положите файл снова."
                           " Если файл правильный — нажмите «Взять как есть».")
        report["suspect"] = stamps
        report["tables"] = [{"key": k, "title": tt["title"], "ok": k in tables, "file": src.get(k),
                             "rows": int(len(tables[k])) if k in tables else None,
                             "period": _period(k, tables[k]) if k in tables else "", "notes": notes.get(k, [])}
                            for k, tt in TABLES.items()]
        _save_report(s, report)
        raise IntakeError(report["error"])
    # продажи и остатки: новые месяцы ложатся поверх прошлой истории, более ранние месяцы сохраняются
    for key in ("sales", "stock"):
        if key in found and old[key] is not None and len(tables[key]):
            first = tables[key]["month"].min()
            keep = old[key][old[key]["month"] < first]
            if len(keep):
                tables[key] = pd.concat([keep[[c for c in keep.columns if c in tables[key].columns]], tables[key]],
                                        ignore_index=True)
                notes.setdefault(key, []).append(
                    f"месяцы раньше {_period(key, pd.DataFrame({'month': [first]}))} — из прошлой выгрузки")
    for k in ("po", "receipts", "suppliers"):
        tables.setdefault(k, None)
    tables = _complete(tables, export_date, notes)

    s.raw_dir.mkdir(parents=True, exist_ok=True)
    for key, t in TABLES.items():
        tables[key].to_csv(s.raw_dir / t["file"], index=False, encoding="utf-8")
    (s.raw_dir / "export_info.json").write_text(json.dumps({"export_date": export_date.isoformat(), "files": report["files"]},
                                                           ensure_ascii=False), encoding="utf-8")
    report["tables"] = [{"key": k, "title": t["title"], "ok": True, "file": src.get(k) or "достроено по другим данным",
                         "rows": int(len(tables[k])), "period": _period(k, tables[k]), "notes": sorted(set(notes.get(k, [])))}
                        for k, t in TABLES.items()]
    report["ok"] = True
    _save_report(s, report)

    # отметить взятые файлы; из своей папки «Входящие» — перенести в «обработано»
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    seen.setdefault("files", {})
    for p in files:
        rec = {"stamp": f"{p.stat().st_size}:{int(p.stat().st_mtime)}", "when": report["when"]}
        if not any(u["file"] == p.name for u in unknown):
            seen["files"][p.name] = rec
        if not custom and not any(u["file"] == p.name for u in unknown):
            done = folder / "обработано"
            done.mkdir(exist_ok=True)
            try:
                shutil.move(str(p), done / f"{stamp}_{p.name}")
                seen["files"].pop(p.name, None)
            except OSError:
                pass
    _write_seen(s, seen)
    return ", ".join(report["files"])


_NAME_DATES = (
    (re.compile(r"(?<!\d)(20\d\d)[-_.](\d\d)[-_.](\d\d)(?!\d)"), (1, 2, 3)),  # 2026-08-13
    (re.compile(r"(?<!\d)(\d\d)[-_.](\d\d)[-_.](20\d\d)(?!\d)"), (3, 2, 1)),  # 13.08.2026
)


def _date_in_name(name: str) -> date | None:
    for rx, (y, m, d) in _NAME_DATES:
        for mt in rx.finditer(name):
            try:
                return date(int(mt.group(y)), int(mt.group(m)), int(mt.group(d)))
            except ValueError:
                continue
    return None


def _export_date(files: list[Path], found: dict) -> tuple[date, str]:
    """Дата выгрузки. Дата изменения файла врёт, если файл скопировали или переслали почтой позже,
    поэтому сначала — дата в имени файла, потом — проверка по самим данным."""
    today = date.today()
    named = [d for p in files if (d := _date_in_name(p.name)) and d <= today]
    if named:
        return max(named), "по имени файла"
    d = min(today, max(datetime.fromtimestamp(p.stat().st_mtime).date() for p in files))
    months = [str(df["month"].max()) for k in ("sales", "stock") for _, df in found.get(k, [])
              if "month" in df and len(df)]
    if months:
        last = pd.Period(max(months), freq="M")
        end = last.end_time.date()
        if d > end + timedelta(days=7):
            # файл сохранён сильно позже последнего месяца в данных — значит, скопирован; выгрузка — на конец месяца
            return end, "по последнему месяцу в данных"
    return d, "по дате файла"


NUM_COLS = ("qty_sold", "qty_returned", "stock_qty", "qty", "qty_ordered", "qty_received", "qty_pending",
            "avg_markup_pct_12m", "median_planned_lead_days", "median_days_between_receipts", "planned_lead_days")


def _old(s: Settings, key: str) -> pd.DataFrame | None:
    """Таблица из прошлой выгрузки (data/raw) или None."""
    f = s.raw_dir / TABLES[key]["file"]
    if not f.exists():
        return None
    df = pd.read_csv(f, dtype={"sku_id": str, "supplier_id": str}, keep_default_na=False)
    for c in df.columns:
        if c in NUM_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _nonzero(df: pd.DataFrame, col: str) -> pd.DataFrame:
    return df[pd.to_numeric(df[col], errors="coerce").fillna(0) != 0] if col in df else df


def _n(v: int) -> str:
    return f"{v:,}".replace(",", "\u00a0")


def _check_complete(tables: dict, old: dict, found: dict) -> list[str]:
    """Признаки неполной выгрузки: товаров заметно меньше, чем в прошлый раз, или артикулы не из справочника."""
    out = []

    def fname(k):
        return ", ".join(f"«{n}»" for n, _ in found[k])

    if "sku" in found and old["sku"] is not None:
        n_new, n_old = tables["sku"]["sku_id"].nunique(), old["sku"]["sku_id"].nunique()
        if n_old >= 20 and n_new < 0.5 * n_old:
            out.append(f"{TABLES['sku']['title']} ({fname('sku')}): товаров в файле — {_n(n_new)}, а в прошлой выгрузке — {_n(n_old)}.")
    for k, col in (("sales", "qty_sold"), ("stock", "stock_qty")):
        if k not in found or old[k] is None or not len(tables[k]):
            continue
        new, prev = _nonzero(tables[k], col), _nonzero(old[k], col)
        if not len(new) or not len(prev):
            continue
        last = new["month"].max()
        ref = prev[prev["month"] == last] if (prev["month"] == last).any() else prev[prev["month"] == prev["month"].max()]
        n_new, n_old = new[new["month"] == last]["sku_id"].nunique(), ref["sku_id"].nunique()
        if n_old >= 20 and n_new < 0.5 * n_old:
            out.append(f"{TABLES[k]['title']} ({fname(k)}): за {_period(k, pd.DataFrame({'month': [last]}))} в файле "
                       f"товаров — {_n(n_new)}, а в прошлой выгрузке — {_n(n_old)}.")
    known = set(tables["sku"]["sku_id"].astype(str)) if "sku" in tables else set()
    for k in ("sales", "stock", "po", "receipts"):
        if k not in found or not known or not len(tables[k]):
            continue
        ids = set(tables[k]["sku_id"].astype(str))
        miss = ids - known
        if len(miss) > 0.5 * len(ids):
            out.append(f"{TABLES[k]['title']} ({fname(k)}): {_n(len(miss))} из {_n(len(ids))} артикулов нет "
                       f"в справочнике товаров (например, {sorted(miss)[0]}).")
    return out


def _stamp(p: Path) -> str:
    st_ = p.stat()
    return f"{p.name}:{st_.st_size}:{int(st_.st_mtime)}"


def _accept_path(s: Settings) -> Path:
    return s.out_dir / "intake_accept.json"


def _accepted(s: Settings) -> set:
    try:
        return set(json.loads(_accept_path(s).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return set()


def accept_suspect(s: Settings) -> bool:
    """«Взять как есть»: запомнить файлы, на которые сработала проверка полноты. False — запоминать нечего."""
    rep = read_report(s) or {}
    if not rep.get("suspect"):
        return False
    s.out_dir.mkdir(parents=True, exist_ok=True)
    _accept_path(s).write_text(json.dumps(sorted(_accepted(s) | set(rep["suspect"])), ensure_ascii=False), encoding="utf-8")
    return True


def _save_report(s: Settings, report: dict) -> None:
    s.out_dir.mkdir(parents=True, exist_ok=True)
    _report_path(s).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def preview(s: Settings) -> dict:
    """Что лежит в папке с выгрузками — без переноса файлов и без пересчёта (для первой настройки)."""
    folder, custom = source_folder(s)
    if not custom:
        folder.mkdir(exist_ok=True)
    files = _new_files(folder, {})
    found, unknown, notes = _scan(files, read_mapping(s)) if files else ({}, [], {})
    info = {}
    try:
        info = json.loads((s.raw_dir / "export_info.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    tables = []
    for key, tt in TABLES.items():
        row = {"key": key, "title": tt["title"], "required": key in REQUIRED_TABLES}
        if key in found:
            df = pd.concat([d for _, d in found[key]], ignore_index=True)
            row |= {"state": "new", "file": ", ".join(n for n, _ in found[key]), "rows": int(len(df)), "period": _period(key, df)}
        elif (s.raw_dir / tt["file"]).exists():
            row |= {"state": "old"}
        else:
            row |= {"state": "none"}
        tables.append(row)
    req = [r for r in tables if r["required"]]
    return {"folder": str(folder), "custom": custom, "files": [p.name for p in files], "unknown": unknown, "tables": tables,
            "ready_new": all(r["state"] == "new" for r in req), "ready": all(r["state"] != "none" for r in req),
            "old_date": info.get("export_date")}
