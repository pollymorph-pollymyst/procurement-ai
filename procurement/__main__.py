"""Запуск: python -m procurement [run|check|package] [--as-of ГГГГ-ММ-ДД] [--no-open]

serve    — открыть дашборд с панелью «Управление» (локальный сервер: настройки, расписание, «Обновить сейчас»)
schedule — on | off | status: автопересчёт по расписанию из config.toml
run      — взять выгрузку из «Входящих», посчитать, сохранить файлы заказов и пересобрать дашборд (по умолчанию)
check    — только проверить данные и показать итоги, ничего не сохранять
package  — собрать zip-архив проекта для отправки
comment  — заново вставить «Комментарий аналитика» (outputs/commentary_override.json) без пересчёта
sku ID   — показать расчёт по одному артикулу (для разбора «почему столько»)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from datetime import date, datetime

from .config import load_settings
from .data import DataError, load, truncate
from .sources import intake


def _baseline_date(as_of: date) -> date:
    y, m = (as_of.year, as_of.month - 1) if as_of.month > 1 else (as_of.year - 1, 12)
    return date(y, m, min(as_of.day, 28))


def _override(s, result) -> dict | None:
    """Комментарий, который написал Claude Code (скилл plan-purchases), если он к этой же дате."""
    ov = s.out_dir / "commentary_override.json"
    if not ov.exists():
        return None
    try:
        data = json.loads(ov.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("commentary_override.json: не JSON — пропускаю", file=sys.stderr)
        return None
    if data.get("as_of") != result["meta"]["as_of"]:
        print(f"commentary_override.json написан к {data.get('as_of')}, а анализ на {result['meta']['as_of']} — пропускаю")
        return None
    return data.get("comment")


def _latest(s) -> dict:
    p = s.out_dir / "latest" / "result.json"
    if not p.exists():
        raise DataError("Расчёта ещё нет. Сначала запустите «Обновить анализ» (python -m procurement run).")
    return json.loads(p.read_text(encoding="utf-8"))


def run(args) -> int:
    """Пересчёт с записью в журнал. При ошибке дашборд пересобирается из прошлого результата,
    чтобы панель «Управление» показала, что пошло не так."""
    from . import control
    from .build import build_dashboard

    s = load_settings()
    t0 = time.time()
    rec = {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "trigger": args.trigger}
    try:
        result, took = _run(args, s)
    except Exception as e:  # noqa: BLE001 — любую ошибку показываем человеку простыми словами
        control.log_run(s, {**rec, "ok": False, "error": control.plain_error(e), "seconds": round(time.time() - t0)})
        try:
            build_dashboard(_latest(s), s)
        except Exception:  # noqa: BLE001
            pass
        raise
    k = result["kpi"]
    control.log_run(s, {**rec, "ok": True, "seconds": round(time.time() - t0), "export": took,
                        "as_of": result["meta"]["as_of"],
                        "counts": {"skus": k["order_now_skus"], "qty": k["order_now_qty"],
                                   "suppliers": k["order_now_suppliers"], "deficit": k["deficit_skus"]}})
    page = build_dashboard(result, s)
    print(f"Готово. Заказать сейчас: {k['order_now_skus']} поз., {k['order_now_qty']} шт., "
          f"поставщиков: {k['order_now_suppliers']}. Не успеем довезти: {k['deficit_skus']} поз.")
    print(f"Дашборд: {page}")
    if not args.no_open:
        webbrowser.open(page.as_uri())
    return 0


def _run(args, s):
    from . import commentary, export, history
    from .pipeline import compute

    from . import moysklad
    pulled = moysklad.pull(s)
    if pulled:
        print(f"Данные из МойСклад загружены: {pulled}")
    took = intake(s)
    if took:
        print(f"Взята новая выгрузка: {took}")
    ds = load(s, args.as_of)
    from .control import add_sent_orders
    ds, sent_orders = add_sent_orders(s, ds)
    if sent_orders:
        print(f"Отправленные заказы, которых ещё нет в выгрузке, считаю «в пути»: {len(sent_orders)} пост.")
    print(f"Дата анализа: {ds.as_of:%d.%m.%Y}. Считаю…")
    result = compute(ds, s)
    result["sent_orders"] = sent_orders

    prev = history.previous(s, result["meta"]["as_of"])
    if prev is None:
        # первый запуск: для сравнения считаем, каким был анализ месяцем раньше на тех же данных
        b = _baseline_date(ds.as_of)
        print(f"Первый запуск — для раздела «Что изменилось» считаю анализ на {b:%d.%m.%Y}…")
        base = compute(truncate(ds, b), s, with_backtest=False)
        history.save(s, history.snapshot(base), label="_baseline")
        prev = history.previous(s, result["meta"]["as_of"])
    cur_snap = history.snapshot(result)
    changes = history.diff(prev, cur_snap, sent_orders)
    result["changes"] = changes

    result["commentary"] = commentary.make(result, s, changes, override=_override(s, result))

    from . import suppliers
    files = export.export_all(result, s.docs_dir / "orders", suppliers.read_info(s))
    result["files"] = files

    latest = s.out_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    (latest / "facts.json").write_text(
        json.dumps(commentary.facts(result, changes), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    history.save(s, cur_snap)
    return result, (getattr(took, "name", str(took)) if took else None)


def check(args) -> int:
    from .pipeline import compute

    s = load_settings()
    ds = load(s, args.as_of)
    r = compute(ds, s, with_backtest=False)
    print(json.dumps({"as_of": r["meta"]["as_of"], **r["kpi"]}, ensure_ascii=False, indent=1))
    return 0


def comment(args) -> int:
    from . import commentary
    from .build import build_dashboard

    s = load_settings()
    result = _latest(s)
    result["commentary"] = commentary.make(result, s, result.get("changes"), override=_override(s, result))
    c = result["commentary"]
    (s.out_dir / "latest" / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    page = build_dashboard(result, s)
    if c.get("note"):
        print(f"Внимание: {c['note']}")
    print(f"Комментарий ({c.get('source', 'правила')}): {c['headline']}")
    print(f"Дашборд: {page}")
    return 0


def sku(args) -> int:
    s = load_settings()
    result = _latest(s)
    want = (args.id or "").upper()
    hit = [x for x in result["skus"] if x["sku_id"].upper() == want]
    if not hit:
        print(f"Артикул {want} не найден. Пример: python -m procurement sku {result['skus'][0]['sku_id']}")
        return 1
    x = dict(hit[0])
    for k in ("history", "forecast", "stock_path"):
        x.pop(k, None)  # длинные ряды по месяцам — в дашборде на графике
    print(json.dumps({"as_of": result["meta"]["as_of"], **x}, ensure_ascii=False, indent=1, default=str))
    return 0


def schedule_cmd(args) -> int:
    """schedule on|off|status — включить/выключить автопересчёт по расписанию из config.toml."""
    from . import schedule
    from . import settings as st

    what = (args.id or "status").lower()
    if what in ("on", "off"):
        sch = st.save_schedule({**st.schedule(), "включено": what == "on"})
        schedule.apply(sch)
        schedule.write_workflow(sch)
    sch, task = st.schedule(), schedule.status()
    days = ", ".join(sch["дни"]) if len(sch["дни"]) < 7 else "каждый день"
    print(f"Расписание: {'включено' if sch['включено'] else 'выключено'}, {sch['время']}, {days}.")
    if task is not None:
        print("На этом компьютере: " + (f"задание есть, следующий запуск {task.get('next') or '—'}"
                                        if task.get("exists") else "задание не создано"))
    return 0


def serve(args) -> int:
    from .server import serve as run_server
    return run_server(open_browser=not args.no_open)


def package(args) -> int:
    from .build import package_zip

    s = load_settings()
    p = package_zip(s)
    print(f"Архив готов: {p}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="procurement", description="Планирование закупок")
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "check", "package", "comment", "sku", "schedule", "serve"])
    ap.add_argument("id", nargs="?", help="артикул для sku; on|off|status для schedule")
    ap.add_argument("--as-of", help="дата анализа ГГГГ-ММ-ДД (по умолчанию — дата выгрузки)")
    ap.add_argument("--no-open", action="store_true", help="не открывать дашборд в браузере")
    ap.add_argument("--trigger", default="github" if os.environ.get("GITHUB_ACTIONS") else "manual",
                    help="кто запустил: manual | panel | schedule | github | claude (для журнала)")
    args = ap.parse_args(argv)
    try:
        return {"run": run, "check": check, "package": package, "comment": comment, "sku": sku,
                "schedule": schedule_cmd, "serve": serve}[args.cmd](args)
    except DataError as e:
        print(f"\nОШИБКА В ДАННЫХ: {e}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
