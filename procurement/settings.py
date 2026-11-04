"""Настройки для панели «Управление»: описание каждого параметра, проверка и запись в config.toml.

config.toml остаётся единственным источником. Запись меняет только значение в строке
«ключ = значение», комментарии и порядок сохраняются.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import fields
from pathlib import Path

from .config import ROOT, Settings, _MAP

# kind: int | float | pct (доля 0..1, в панели — проценты) | text | select | date
# level: main — «Главное», extra — «Для специалиста»
FIELDS: list[dict] = [
    # ---- главное
    dict(section="закупка", key="минимальная_наценка", kind="float", unit="%", min=0, max=300, step=1, level="main",
         group="Что заказывать",
         label="Минимальная наценка",
         help="Товары с наценкой ниже не попадают в заказ, а уходят в список «Пересмотреть цену»."),
    dict(section="сервис", key="A", kind="pct", unit="%", min=50, max=99.9, step=0.5, level="main", group="Сколько заказывать",
         label="Надёжность наличия: важные товары",
         help="С какой вероятностью важный товар (первые 80% продаж) не кончится до следующего прихода. Выше — больше запас."),
    dict(section="сервис", key="B", kind="pct", unit="%", min=50, max=99.9, step=0.5, level="main", group="Сколько заказывать",
         label="Надёжность наличия: средние товары", help="То же для следующих 15% продаж."),
    dict(section="сервис", key="C", kind="pct", unit="%", min=50, max=99.9, step=0.5, level="main", group="Сколько заказывать",
         label="Надёжность наличия: остальные товары", help="То же для самых редко продаваемых товаров."),
    dict(section="сервис", key="страховой_запас_макс_доля", kind="pct", unit="%", min=10, max=300, step=5, level="main",
         group="Сколько заказывать",
         label="Страховой запас — не больше",
         help="Потолок резерва в процентах от спроса за цикл заказа. Защищает от раздувания запаса по товарам с рваными продажами."),
    dict(section="закупка", key="цикл_заказа_мин", kind="int", unit="дн.", min=7, max=365, step=1, level="main",
         group="Сроки", label="Как часто заказываем у поставщика: не чаще чем раз в",
         help="Партия рассчитывается до следующего заказа. Реальный цикл берётся из истории приходов в этих пределах."),
    dict(section="закупка", key="цикл_заказа_макс", kind="int", unit="дн.", min=7, max=365, step=1, level="main",
         group="Сроки", label="…и не реже чем раз в", help=""),
    dict(section="закупка", key="срок_поставки_по_умолчанию", kind="int", unit="дн.", min=7, max=365, step=1,
         level="main", group="Сроки", label="Срок поставки нового поставщика",
         help="Если у поставщика ещё не было приходов, считаем, что товар идёт столько дней."),
    dict(section="закупка", key="избыток_месяцев", kind="int", unit="мес.", min=2, max=60, step=1, level="main",
         group="Риски", label="Избыток — если запаса хватает больше чем на",
         help="Такие товары попадают в список «Избыток» и не заказываются."),
    dict(section="анализ", key="горизонт_месяцев", kind="int", unit="мес.", min=6, max=36, step=1, level="main",
         group="Риски", label="Строить план на", help="Насколько вперёд считать прогноз и календарь заказов."),

    # ---- для специалиста
    dict(section="анализ", key="дата_анализа", kind="date", level="extra", group="Данные",
         label="Дата анализа", help="«авто» — по последней выгрузке. Можно указать дату ГГГГ-ММ-ДД, чтобы посмотреть, что система советовала бы тогда."),
    dict(section="анализ", key="день_выгрузки", kind="int", unit="число", min=1, max=31, step=1, level="extra", group="Данные",
         label="По какое число выгружен последний неполный месяц", help="Из описания выгрузки ERP."),
    dict(section="закупка", key="срок_поставки_местный", kind="int", unit="дн.", min=1, max=180, step=1, level="extra",
         group="Сроки", label="Срок поставки местного поставщика", help=""),
    dict(section="закупка", key="срок_поставки_мин", kind="int", unit="дн.", min=1, max=365, step=1, level="extra",
         group="Сроки", label="Срок поставки: не меньше", help="Фактические сроки из истории обрезаются в эти границы."),
    dict(section="закупка", key="срок_поставки_макс", kind="int", unit="дн.", min=1, max=365, step=1, level="extra",
         group="Сроки", label="Срок поставки: не больше", help=""),
    dict(section="закупка", key="просрочка_сомнительного_заказа", kind="int", unit="дн.", min=7, max=365, step=1,
         level="extra", group="Заказы в пути", label="Заказ в пути считается сомнительным после просрочки в",
         help="Сомнительный заказ не входит в запас — его нужно уточнить у поставщика."),
    dict(section="закупка", key="ожидание_просроченного", kind="int", unit="дн.", min=1, max=120, step=1, level="extra",
         group="Заказы в пути", label="Просроченный заказ ждём ещё", help="Через сколько дней ожидаем приход по просроченному (но не сомнительному) заказу."),
    dict(section="закупка", key="учитывать_недовоз", kind="select", level="main", group="Заказы в пути",
         options=[["да", "Да — ждём столько, сколько поставщик обычно довозит"], ["нет", "Нет — ждём заказ целиком"]],
         label="Учитывать недовоз поставщиков",
         help="Если поставщик по прошлым заказам довозил 40%, из 1 000 шт. в пути рассчитываем на 400. Нужно минимум 3 закрытых заказа."),
    dict(section="закупка", key="недовоз_не_ниже", kind="pct", unit="%", min=5, max=100, step=5, level="extra",
         group="Заказы в пути", label="Даже у ненадёжного поставщика ждём не меньше",
         help="Нижняя граница доли, которую ждём из заказа в пути."),
    dict(section="праздники", key="учитывать", kind="select", level="main", group="Праздники у поставщиков",
         options=[["да", "Да — простой фабрик удлиняет срок поставки"], ["нет", "Нет — праздники не учитывать"]],
         label="Учитывать праздники у поставщиков",
         help="Китайский Новый год, Золотая неделя, Дивали и другие. Какие праздники и у каких поставщиков — ниже, в «Праздники у поставщиков»."),
    dict(section="закупка", key="шаг_округления_по_умолчанию", kind="int", unit="шт.", min=1, max=1000, step=1,
         level="extra", group="Что заказывать", label="Шаг округления заказа",
         help="Если в истории заказов товара нет круглых количеств."),
    dict(section="закупка", key="редкий_спрос_шт_в_год", kind="float", unit="шт./год", min=0, max=500, step=1,
         level="extra", group="Что заказывать", label="Редкий спрос — продаётся меньше чем",
         help="Такие товары не держим на складе, заказываем под клиента."),
    dict(section="сервис", key="граница_A", kind="pct", unit="%", min=50, max=99, step=1, level="extra",
         group="Важность товаров", label="Важные товары — первые … продаж", help=""),
    dict(section="сервис", key="граница_B", kind="pct", unit="%", min=51, max=99.9, step=0.5, level="extra",
         group="Важность товаров", label="Важные и средние — первые … продаж", help=""),
    dict(section="сервис", key="неуверенность_уровня", kind="pct", unit="%", min=0, max=100, step=1, level="extra",
         group="Прогноз", label="Запас на ошибку в уровне спроса", help="Добавляется к страховому запасу."),
    dict(section="прогноз", key="окно_уровня_месяцев", kind="int", unit="мес.", min=3, max=36, step=1, level="extra",
         group="Прогноз", label="Уровень спроса считаем по последним", help=""),
    dict(section="прогноз", key="сила_тренда", kind="pct", unit="%", min=0, max=100, step=5, level="extra",
         group="Прогноз", label="Насколько учитывать рост или падение год к году", help="0% — не учитывать, 100% — полностью."),
    dict(section="прогноз", key="тренд_мин", kind="float", unit="×", min=0.1, max=1, step=0.05, level="extra",
         group="Прогноз", label="Тренд: не ниже", help="Во сколько раз спрос может упасть за год в прогнозе."),
    dict(section="прогноз", key="тренд_макс", kind="float", unit="×", min=1, max=5, step=0.05, level="extra",
         group="Прогноз", label="Тренд: не выше", help="Во сколько раз спрос может вырасти за год в прогнозе."),
    dict(section="прогноз", key="мало_истории_месяцев", kind="int", unit="мес.", min=1, max=24, step=1, level="extra",
         group="Прогноз", label="Мало истории — меньше", help="Такие товары помечаются: прогноз по ним менее надёжен."),
]

PAIRS = [  # (меньшее, большее, текст ошибки)
    (("закупка", "цикл_заказа_мин"), ("закупка", "цикл_заказа_макс"), "Цикл заказа: «не чаще» должно быть не больше «не реже»."),
    (("закупка", "срок_поставки_мин"), ("закупка", "срок_поставки_макс"), "Срок поставки: «не меньше» должно быть не больше «не больше»."),
    (("сервис", "граница_A"), ("сервис", "граница_B"), "Граница важных товаров должна быть меньше границы средних."),
    (("прогноз", "тренд_мин"), ("прогноз", "тренд_макс"), "Тренд: нижняя граница должна быть меньше верхней."),
]

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SCHEDULE_DEFAULT = {"включено": True, "время": "07:00", "дни": DAYS[:], "часовой_пояс": 3}


def _defaults() -> dict:
    d = {f.name: f.default for f in fields(Settings) if not callable(f.default)}
    return {k: d.get(attr) for k, attr in _MAP.items()}


def config_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "config.toml"


def read_raw(path: Path | None = None) -> dict:
    with open(path or config_path(), "rb") as f:
        return tomllib.load(f)


def schema(path: Path | None = None) -> dict:
    """Всё для панели: поля с текущими и стандартными значениями, категории, расписание."""
    raw = read_raw(path)
    dflt = _defaults()
    out = []
    for f in FIELDS:
        k = (f["section"], f["key"])
        out.append({**f, "id": f"{f['section']}.{f['key']}",
                    "value": raw.get(f["section"], {}).get(f["key"], dflt.get(k)), "default": dflt.get(k)})
    return {"fields": out, "categories": raw.get("категории", {}), "schedule": schedule(raw)}


def schedule(raw: dict | None = None) -> dict:
    raw = raw if raw is not None else read_raw()
    sch = {**SCHEDULE_DEFAULT, **raw.get("расписание", {})}
    sch["дни"] = [d for d in DAYS if d in sch.get("дни", [])]
    return sch


# ------------------------------------------------------------------ проверка

class SettingsError(ValueError):
    pass


def _coerce(f: dict, v):
    if f["kind"] == "date":
        v = str(v).strip().lower() or "авто"
        if v != "авто" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise SettingsError(f"«{f['label']}»: нужно «авто» или дата вида 2026-08-13.")
        return v
    if f["kind"] == "select":
        allowed = [o[0] for o in f["options"]]
        if v not in allowed:
            raise SettingsError(f"«{f['label']}»: выберите одно из значений.")
        return v
    try:
        x = float(str(v).replace(",", ".").replace(" ", ""))
    except ValueError:
        raise SettingsError(f"«{f['label']}»: нужно число.") from None
    if not (f["min"] <= x <= f["max"]):
        raise SettingsError(f"«{f['label']}»: допустимо от {f['min']:g} до {f['max']:g} {f.get('unit', '')}".strip() + ".")
    if f["kind"] == "int":
        return int(round(x))
    if f["kind"] == "pct":
        return round(x / 100, 4)  # в панели проценты, в файле доля
    return int(x) if x.is_integer() else round(x, 4)


def validate(values: dict) -> dict[tuple, object]:
    """values: {"раздел.ключ": значение как в панели}. Возвращает {(раздел, ключ): значение для файла}."""
    by_id = {f"{f['section']}.{f['key']}": f for f in FIELDS}
    out = {}
    for fid, v in values.items():
        f = by_id.get(fid)
        if f is None:
            raise SettingsError(f"Неизвестная настройка: {fid}")
        out[(f["section"], f["key"])] = _coerce(f, v)
    merged = {**{(f["section"], f["key"]): f.get("value") for f in schema()["fields"]}, **out}
    for lo, hi, msg in PAIRS:
        if merged.get(lo) is not None and merged.get(hi) is not None and merged[lo] > merged[hi]:
            raise SettingsError(msg)
    return out


def validate_schedule(sch: dict) -> dict:
    t = str(sch.get("время", "08:00")).strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        raise SettingsError("Время запуска — в виде 08:00.")
    days = [d for d in DAYS if d in (sch.get("дни") or [])]
    if not days:
        raise SettingsError("Выберите хотя бы один день недели.")
    tz = int(sch.get("часовой_пояс", 3))
    if not -12 <= tz <= 14:
        raise SettingsError("Часовой пояс — от −12 до +14.")
    return {"включено": bool(sch.get("включено")), "время": t, "дни": days, "часовой_пояс": tz}


# ------------------------------------------------------------------ запись с сохранением комментариев

def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def _key_re(key: str) -> re.Pattern:
    k = re.escape(key)
    return re.compile(rf'^(\s*)("{k}"|{k})(\s*=\s*)(.*?)(\s*#.*)?$')


def set_value(text: str, section: str, key: str, value) -> str:
    lines = text.splitlines()
    head = re.compile(r'^\s*\[\s*"?' + re.escape(section) + r'"?\s*\]\s*$')
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
    if start is None:
        return text.rstrip("\n") + f'\n\n["{section}"]\n"{key}" = {_toml_value(value)}\n'
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    rx = _key_re(key)
    for i in range(start + 1, end):
        m = rx.match(lines[i])
        if m:
            lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{_toml_value(value)}{m.group(5) or ''}"
            return "\n".join(lines) + "\n"
    last = max((i for i in range(start + 1, end) if lines[i].strip() and not lines[i].lstrip().startswith("#")),
               default=start)
    lines.insert(last + 1, f'"{key}" = {_toml_value(value)}')
    return "\n".join(lines) + "\n"


def save(values: dict[tuple, object], path: Path | None = None) -> None:
    path = path or config_path()
    text = path.read_text(encoding="utf-8")
    for (section, key), v in values.items():
        text = set_value(text, section, key, v)
    tomllib.loads(text)  # не записываем файл, который потом не прочитается
    path.write_text(text, encoding="utf-8")


def reset_to_defaults(level: str | None = None, path: Path | None = None) -> None:
    dflt = _defaults()
    save({(f["section"], f["key"]): dflt[(f["section"], f["key"])] for f in FIELDS
          if (level is None or f["level"] == level) and dflt.get((f["section"], f["key"])) is not None}, path)


def save_schedule(sch: dict, path: Path | None = None) -> dict:
    sch = validate_schedule(sch)
    save({("расписание", k): v for k, v in sch.items()}, path)
    return sch


def save_categories(names: dict, path: Path | None = None) -> None:
    cur = read_raw(path).get("категории", {})
    vals = {}
    for code, name in names.items():
        name = str(name).strip()
        if code not in cur:
            raise SettingsError(f"Неизвестная категория: {code}")
        if not name or len(name) > 60:
            raise SettingsError("Название категории — от 1 до 60 знаков.")
        vals[("категории", code)] = name
    save(vals, path)
