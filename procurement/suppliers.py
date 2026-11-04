"""Карточки поставщиков: название, язык файла заказа и контакты.

Название и язык — в suppliers.json в корне проекта: это общие настройки, они публикуются вместе
с проектом. Контакты — только на компьютере закупщика, в outputs/contacts.json (файл не попадает
в репозиторий и не встраивается в опубликованный дашборд): их отдаёт лишь локальный сервер.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Settings

LANGS = {"ru": "Русский", "en": "English", "zh": "中文"}

# вид контакта: подпись, подсказка в поле ввода
KINDS = {
    "email": ("Почта", "name@company.com"),
    "phone": ("Телефон", "+86 138 0000 0000"),
    "whatsapp": ("WhatsApp", "+86 138 0000 0000"),
    "telegram": ("Telegram", "@username или телефон"),
    "wechat": ("WeChat", "WeChat ID"),
    "site": ("Сайт", "company.com"),
    "other": ("Другое", "ссылка или текст"),
}
MAX_CONTACTS = 12


class SupplierError(ValueError):
    pass


def info_path(s: Settings) -> Path:
    return s.root / "suppliers.json"


def contacts_path(s: Settings) -> Path:
    return s.out_dir / "contacts.json"


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def read_info(s: Settings) -> dict:
    """{supplier_id: {"name": str, "lang": "ru"|"en"|"zh"}} — только заполненное."""
    out = {}
    for k, v in _read(info_path(s)).items():
        if isinstance(v, dict):
            name = str(v.get("name") or "").strip()
            lang = v.get("lang") if v.get("lang") in LANGS else "ru"
            if name or lang != "ru":
                out[str(k)] = {"name": name, "lang": lang}
    return out


def read_contacts(s: Settings) -> dict:
    out = {}
    for k, v in _read(contacts_path(s)).items():
        if isinstance(v, list):
            out[str(k)] = [c for c in v if isinstance(c, dict) and c.get("kind") in KINDS and c.get("value")]
    return out


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(v, limit: int, what: str) -> str:
    v = _CTRL.sub(" ", str(v or "")).strip()
    if len(v) > limit:
        raise SupplierError(f"{what}: не длиннее {limit} знаков.")
    return v


def _check_contact(c: dict) -> dict:
    kind = c.get("kind")
    if kind not in KINDS:
        raise SupplierError("Неизвестный вид контакта.")
    value = _clean(c.get("value"), 200, KINDS[kind][0])
    if not value:
        return {}
    note = _clean(c.get("note"), 60, "Подпись к контакту")
    digits = re.sub(r"\D", "", value)
    if kind in ("phone", "whatsapp") and not 6 <= len(digits) <= 15:
        raise SupplierError(f"{KINDS[kind][0]}: укажите номер с кодом страны, например +86 138 0000 0000.")
    if kind == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise SupplierError(f"Почта «{value}» выглядит неверно.")
    return {"kind": kind, "value": value, **({"note": note} if note else {})}


def save(s: Settings, body: dict, known: set[str] | None = None) -> None:
    """body: {"supplier_id", "name", "lang", "contacts": [{"kind", "value", "note"}]}."""
    sid = _clean(body.get("supplier_id"), 60, "Код поставщика")
    if not sid or (known is not None and sid not in known):
        raise SupplierError("Нет такого поставщика.")
    name = _clean(body.get("name"), 80, "Название")
    lang = body.get("lang") or "ru"
    if lang not in LANGS:
        raise SupplierError("Неизвестный язык файла заказа.")
    raw = body.get("contacts") or []
    if not isinstance(raw, list) or len(raw) > MAX_CONTACTS:
        raise SupplierError(f"Контактов — не больше {MAX_CONTACTS}.")
    contacts = [c for c in (_check_contact(c) for c in raw if isinstance(c, dict)) if c]

    info = _read(info_path(s))
    if name or lang != "ru":
        info[sid] = {"name": name, "lang": lang}
    else:
        info.pop(sid, None)
    _write(info_path(s), info)

    allc = _read(contacts_path(s))
    if contacts:
        allc[sid] = contacts
    else:
        allc.pop(sid, None)
    _write(contacts_path(s), allc)


KIND_LABELS = {
    "en": {"email": "Email", "phone": "Phone", "site": "Website", "other": "Other"},
    "zh": {"email": "邮箱", "phone": "电话", "wechat": "微信", "site": "网站", "other": "其他"},
}


def contact_line(c: dict, lang: str = "ru") -> str:
    """Контакт одной строкой для файла заказа — подпись на языке файла."""
    label = KIND_LABELS.get(lang, {}).get(c["kind"]) or KINDS[c["kind"]][0]
    return f"{label}: {c['value']}" + (f" ({c['note']})" if c.get("note") else "")


def schema() -> dict:
    return {"langs": LANGS, "kinds": {k: {"label": a, "hint": b} for k, (a, b) in KINDS.items()},
            "tpl": {"names": TPL_NAMES, "fields": TPL_FIELDS, "std": TPL}}


# ------------------------------------------------------------------ файл заказа на языке поставщика

T = {
    "ru": {
        "sheet": "Заказ", "title": "Заказ поставщику {who} от {date}",
        "count": "Позиций: {skus}, всего {qty} шт.", "weight": ", вес около {kg} кг",
        "need": "Нужно к {date}", "lead": "срок поставки {days} дн.", "hol": " и {days} дн. праздников",
        "urgent_note": "Позиции с пометкой «Срочно» нужны как можно раньше: если можно, отгрузите их первыми.",
        "contacts": "Контакты: {list}", "total": "Итого", "yes": "да",
        "cols": {"sku_id": "Артикул", "category_name": "Товар", "brand_id": "Бренд", "qty": "Количество, шт.",
                 "round_step": "Кратность", "urgent": "Срочно", "arrival_date": "Нужно к"},
    },
    "en": {
        "sheet": "Order", "title": "Purchase order to {who}, {date}",
        "count": "Items: {skus}, total {qty} pcs", "weight": ", approx. {kg} kg",
        "need": "Required by {date}", "lead": "lead time {days} days", "hol": " plus {days} holiday days",
        "urgent_note": "Items marked “Urgent” are needed as soon as possible: please ship them first if you can.",
        "contacts": "Contacts: {list}", "total": "Total", "yes": "yes",
        "cols": {"sku_id": "SKU", "category_name": "Product", "brand_id": "Brand", "qty": "Quantity, pcs",
                 "round_step": "Pack size", "urgent": "Urgent", "arrival_date": "Required by"},
    },
    "zh": {
        "sheet": "订单", "title": "采购订单：{who}，{date}",
        "count": "品项：{skus}，共 {qty} 件", "weight": "，约 {kg} 公斤",
        "need": "要求到货日期：{date}", "lead": "交货期 {days} 天", "hol": "，另加 {days} 天假期",
        "urgent_note": "标记为“加急”的品项请尽快安排，如可能请优先发货。",
        "contacts": "联系方式：{list}", "total": "合计", "yes": "是",
        "cols": {"sku_id": "货号", "category_name": "产品", "brand_id": "品牌", "qty": "数量（件）",
                 "round_step": "包装倍数", "urgent": "加急", "arrival_date": "要求到货"},
    },
}
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]


def fmt_date(iso, lang: str) -> str:
    from .plan import ru_date
    if not iso:
        return "—"
    if lang == "ru":
        return ru_date(iso)
    y, m, d = (int(x) for x in str(iso)[:10].split("-"))
    return f"{d} {EN_MONTHS[m - 1]} {y}" if lang == "en" else f"{y}年{m}月{d}日"


# ------------------------------------------------------------------ шаблоны сообщений поставщику
# Стандартные тексты — здесь, на трёх языках. Изменённые закупщиком хранятся только на его компьютере
# (outputs/templates.json, рядом с контактами): в шаблон можно вписать подпись с телефоном.
# Подстановки в фигурных скобках заполняет дашборд по текущему заказу.

TPL_NAMES = {"order": "Отправляем заказ", "dates": "Уточнить сроки отгрузки", "prices": "Запросить цены и наличие"}
TPL_FIELDS = {
    "поставщик": "название поставщика", "дата": "дата заказа", "позиций": "сколько позиций",
    "штук": "сколько штук всего", "нужно_к": "к какому дню нужен товар", "товары": "список товаров с количеством",
}
TPL = {
    "ru": {
        "order": {"subject": "Заказ от {дата}: {позиций} поз., {штук} шт.",
                  "body": "Здравствуйте!\n\nОтправляем заказ: {позиций} поз., всего {штук} шт. Файл заказа — во вложении.\n"
                          "Товар нужен к {нужно_к}. Позиции с пометкой «срочно» просим отгрузить первыми.\n\n{товары}\n\n"
                          "Подтвердите, пожалуйста, что заказ получен, и напишите дату отгрузки.\n\nСпасибо!"},
        "dates": {"subject": "Сроки отгрузки заказа",
                  "body": "Здравствуйте!\n\nПодскажите, пожалуйста, когда сможете отгрузить заказ: {позиций} поз., {штук} шт.? "
                          "Нам нужно получить товар к {нужно_к}.\n\nЕсли часть будет готова раньше, сообщите — можем забрать "
                          "частями.\n\nСпасибо!"},
        "prices": {"subject": "Цены и наличие",
                   "body": "Здравствуйте!\n\nПришлите, пожалуйста, актуальные цены и наличие по этим позициям:\n\n{товары}\n\n"
                           "Если чего-то нет на складе — напишите срок производства.\n\nСпасибо!"},
    },
    "en": {
        "order": {"subject": "Purchase order {дата}: {позиций} items, {штук} pcs",
                  "body": "Hello,\n\nPlease find our purchase order attached: {позиций} items, {штук} pcs in total.\n"
                          "We need the goods by {нужно_к}. Please ship the items marked “urgent” first.\n\n{товары}\n\n"
                          "Kindly confirm receipt of the order and let us know the shipping date.\n\nThank you!"},
        "dates": {"subject": "Shipping date for our order",
                  "body": "Hello,\n\nCould you please tell us when you can ship our order: {позиций} items, {штук} pcs? "
                          "We need to receive the goods by {нужно_к}.\n\nIf part of it is ready earlier, please let us know — "
                          "partial shipment is fine.\n\nThank you!"},
        "prices": {"subject": "Prices and availability",
                   "body": "Hello,\n\nCould you please send current prices and availability for these items:\n\n{товары}\n\n"
                           "If something is out of stock, please tell us the production time.\n\nThank you!"},
    },
    "zh": {
        "order": {"subject": "采购订单 {дата}：{позиций} 个品项，共 {штук} 件",
                  "body": "您好！\n\n附件是我们的采购订单：{позиций} 个品项，共 {штук} 件。\n"
                          "我们需要在 {нужно_к} 前收到货物。标记“加急”的品项请优先发货。\n\n{товары}\n\n"
                          "请确认收到订单，并告知发货日期。\n\n谢谢！"},
        "dates": {"subject": "订单发货日期",
                  "body": "您好！\n\n请问我们的订单（{позиций} 个品项，共 {штук} 件）什么时候可以发货？"
                          "我们需要在 {нужно_к} 前收到货物。\n\n如果部分货物可以提前备好，请告诉我们，可以分批发货。\n\n谢谢！"},
        "prices": {"subject": "价格和库存",
                   "body": "您好！\n\n请发给我们以下品项的最新价格和库存情况：\n\n{товары}\n\n如有缺货，请告知生产周期。\n\n谢谢！"},
    },
}


def templates_path(s: Settings) -> Path:
    return s.out_dir / "templates.json"


def read_templates(s: Settings) -> dict:
    """Изменённые закупщиком шаблоны: {lang: {key: {"subject", "body"}}}."""
    out = {}
    for lang, by in _read(templates_path(s)).items():
        if lang in LANGS and isinstance(by, dict):
            for key, t in by.items():
                if key in TPL_NAMES and isinstance(t, dict):
                    out.setdefault(lang, {})[key] = {"subject": str(t.get("subject") or ""), "body": str(t.get("body") or "")}
    return out


def save_template(s: Settings, body: dict) -> dict:
    """body: {"lang", "key", "subject", "body"}; пустые или как стандартные — вернуть стандартный текст."""
    lang, key = body.get("lang"), body.get("key")
    if lang not in LANGS or key not in TPL_NAMES:
        raise SupplierError("Нет такого шаблона.")
    subject = _clean(body.get("subject"), 150, "Тема")
    text = str(body.get("body") or "").replace("\r\n", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text).strip()
    if len(text) > 3000:
        raise SupplierError("Текст: не длиннее 3000 знаков.")
    data = _read(templates_path(s))
    by = data.get(lang) if isinstance(data.get(lang), dict) else {}
    std = TPL[lang][key]
    if (not subject and not text) or (subject == std["subject"] and text == std["body"]):
        by.pop(key, None)
    else:
        if not text:
            raise SupplierError("Текст сообщения пустой.")
        by[key] = {"subject": subject, "body": text}
    if by:
        data[lang] = by
    else:
        data.pop(lang, None)
    _write(templates_path(s), data)
    return read_templates(s)
