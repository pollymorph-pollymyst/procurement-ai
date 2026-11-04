"""Файлы заказов для поставщиков (Excel) и общий файл со всеми списками."""
from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .plan import ru_date

HEAD_FILL = PatternFill("solid", fgColor="E9EEF5")
WARN_FILL = PatternFill("solid", fgColor="FBE3DF")
BOLD = Font(bold=True)

ORDER_COLS = [
    ("Артикул", "sku_id", 14), ("Категория", "category_name", 24), ("Бренд", "brand_id", 13),
    ("Заказать, шт.", "qty", 13), ("Кратность", "round_step", 10), ("На складе", "on_hand", 10),
    ("В пути", "pipeline", 9), ("Хватит до", "cover_until", 14), ("Придёт к", "arrival_date", 14),
    ("Важность", "abc", 9), ("Почему", "reason", 90),
]
# в файл поставщику — только то, что нужно ему: без остатков, «почему» и внутренних пометок
SUPPLIER_COLS = [
    ("Артикул", "sku_id", 14), ("Товар", "category_name", 28), ("Бренд", "brand_id", 13),
    ("Количество, шт.", "qty", 14), ("Кратность", "round_step", 10), ("Срочно", "urgent", 9),
    ("Нужно к", "arrival_date", 16),
]
LIST_COLS = [
    ("Артикул", "sku_id", 14), ("Категория", "category_name", 24), ("Поставщик", "supplier_id", 13),
    ("Наценка, %", "markup", 11), ("На складе", "on_hand", 10), ("В пути", "pipeline", 9),
    ("Спрос за год, шт.", "demand_12m", 14), ("Было бы к заказу", "qty", 14), ("Лишнее, шт.", "excess_qty", 12),
    ("Почему", "reason", 90),
]
DATE_KEYS = {"cover_until", "arrival_date"}


def _filled(cols, rows):
    """Колонки, где хоть что-то есть: пустой «Бренд» (в выгрузке его нет) в файл не попадает."""
    return [c for c in cols if any(r.get(c[1]) not in (None, "", []) for r in rows)] if rows else cols


def _sheet(ws, cols, rows, title_lines, warn_key=None):
    cols = _filled(cols, rows)
    r0 = 1
    for t in title_lines:
        ws.cell(row=r0, column=1, value=t).font = BOLD if r0 == 1 else Font()
        r0 += 1
    r0 += 1
    for j, (h, _, w) in enumerate(cols, 1):
        c = ws.cell(row=r0, column=j, value=h)
        c.font, c.fill = BOLD, HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    for i, row in enumerate(rows, r0 + 1):
        for j, (_, k, _) in enumerate(cols, 1):
            v = row.get(k)
            if k in DATE_KEYS:
                v = ru_date(v) if v else "—"
            if isinstance(v, list):
                v = "; ".join(v)
            c = ws.cell(row=i, column=j, value=v)
            c.alignment = Alignment(wrap_text=(k == "reason"), vertical="top")
            if warn_key and row.get(warn_key):
                c.fill = WARN_FILL
    ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(cols))}{r0 + max(1, len(rows))}"
    return cols


def _lead_text(g: dict) -> str:
    if not g.get("lead_days"):
        return ""
    hol = (f" и {g['holiday_days']} дн. праздников ({g['holiday']})" if g.get("holiday_days") and g.get("holiday")
           else f" и {g['holiday_days']} дн. праздников" if g.get("holiday_days") else "")
    return f"срок поставки {g['lead_days']} дн.{hol}"


def supplier_file(group: dict, as_of: str, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Заказ"
    lines = [l | {"urgent": "да" if l.get("deficit") else ""} for l in group["lines"]]
    lead = _lead_text(group)
    title = [
        f"Заказ поставщику {group['supplier_id']} от {ru_date(as_of)}",
        f"Позиций: {group['skus']}, всего {group['qty']} шт."
        + (f", вес около {group['weight_kg']} кг" if group.get("weight_kg") else ""),
        f"Нужно к {ru_date(group['arrival_date'])}" + (f" ({lead})" if lead else "") if group.get("arrival_date") else "",
        "Позиции с пометкой «Срочно» нужны как можно раньше: если можно, отгрузите их первыми." if group["deficit"] else "",
    ]
    cols = _sheet(ws, SUPPLIER_COLS, lines, [t for t in title if t], warn_key="deficit")
    total_row = ws.max_row + 2
    ws.cell(row=total_row, column=1, value="Итого").font = BOLD
    qcol = next(j for j, c in enumerate(cols, 1) if c[1] == "qty")
    ws.cell(row=total_row, column=qcol, value=group["qty"]).font = BOLD
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def all_file(result: dict, path: Path) -> Path:
    as_of = result["meta"]["as_of"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    k = result["kpi"]
    summary = [
        ("Дата анализа", ru_date(as_of)),
        ("Заказать сейчас: товаров", k["order_now_skus"]),
        ("Заказать сейчас: штук", k["order_now_qty"]),
        ("Поставщиков", k["order_now_suppliers"]),
        ("Не успеем довезти (товаров)", k["deficit_skus"]),
        ("Нет на складе сейчас", k["out_of_stock_skus"]),
        ("Избыток: товаров / штук", f"{k['excess_skus']} / {k['excess_qty']}"),
        ("Пересмотреть цену", k["review_price_skus"]),
        ("Нужен поставщик", k["need_supplier_skus"]),
        ("Редкий спрос", k["rare_skus"]),
    ]
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 18
    ws.cell(row=1, column=1, value="Итоги анализа закупок").font = Font(bold=True, size=13)
    for i, (a, b) in enumerate(summary, 3):
        ws.cell(row=i, column=1, value=a)
        ws.cell(row=i, column=2, value=b).font = BOLD
    r = len(summary) + 5
    ws.cell(row=r, column=1, value="По поставщикам").font = BOLD
    for j, h in enumerate(["Поставщик", "Позиций", "Штук", "Не успеем довезти", "Придёт к"], 1):
        c = ws.cell(row=r + 1, column=j, value=h)
        c.font, c.fill = BOLD, HEAD_FILL
    for i, g in enumerate(result["order_now"], r + 2):
        for j, v in enumerate([g["supplier_id"], g["skus"], g["qty"], g["deficit"], ru_date(g["arrival_date"])], 1):
            ws.cell(row=i, column=j, value=v)
    for col in "CDE":
        ws.column_dimensions[col].width = 18

    lines = [l | {"supplier_id": g["supplier_id"]} for g in result["order_now"] for l in g["lines"]]
    cols = [("Поставщик", "supplier_id", 13)] + ORDER_COLS
    _sheet(wb.create_sheet("Заказать сейчас"), cols, lines,
           [f"Все заказы на {ru_date(as_of)}", "Красным — товары, которые закончатся раньше прихода."],
           warn_key="deficit")
    soon = [l | {"supplier_id": g["supplier_id"], "order_date_ru": ru_date(l["order_date"])}
            for g in result["order_soon"] for l in g["lines"]]
    _sheet(wb.create_sheet("В ближайшие 30 дней"),
           [("Поставщик", "supplier_id", 13), ("Заказать до", "order_date_ru", 16)] + ORDER_COLS, soon,
           ["Эти заказы можно разместить в ближайший месяц"])
    L = result["lists"]
    m = result["meta"]["settings"]
    _sheet(wb.create_sheet("Пересмотреть цену"), LIST_COLS, L["review_price"],
           [f"Наценка ниже {m['min_markup_pct']:.0f}% — в заказ автоматически не попадают"])
    _sheet(wb.create_sheet("Нужен поставщик"), LIST_COLS, L["need_supplier"],
           ["Пора заказывать, но у товара не указан поставщик"])
    _sheet(wb.create_sheet("Редкий спрос"), LIST_COLS, L["rare"],
           [f"Меньше {m['rare_per_year']:.0f} шт. в год — заказывать под клиента"])
    _sheet(wb.create_sheet("Избыток"), LIST_COLS, L["excess"],
           [f"Запаса больше чем на {m['excess_months']} мес."])
    _sheet(wb.create_sheet("Мёртвый остаток"), LIST_COLS, L["dead"],
           ["Нет продаж больше 12 месяцев, но товар лежит на складе"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def csv_file(result: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        cols = _filled(ORDER_COLS, [l for g in result["order_now"] for l in g["lines"]])
        w.writerow(["Поставщик"] + [h for h, _, _ in cols])
        for g in result["order_now"]:
            for l in g["lines"]:
                w.writerow([g["supplier_id"]] + [
                    (ru_date(l.get(k)) if k in DATE_KEYS else l.get(k)) for _, k, _ in cols])
    return path


def export_all(result: dict, out: Path) -> dict:
    """Сохраняет файлы в out/ и возвращает {supplier_id: имя файла, '_all': ..., '_csv': ...}."""
    as_of = result["meta"]["as_of"]
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.xlsx"):
        old.unlink()
    for old in out.glob("*.csv"):
        old.unlink()
    files = {}
    for g in result["order_now"]:
        name = f"zakaz_{g['supplier_id'] or 'bez_postavshika'}_{as_of}.xlsx"
        supplier_file(g, as_of, out / name)
        files[g["supplier_id"]] = name
    files["_all"] = all_file(result, out / f"vse_zakazy_{as_of}.xlsx").name
    files["_csv"] = csv_file(result, out / f"vse_zakazy_{as_of}.csv").name
    return files
