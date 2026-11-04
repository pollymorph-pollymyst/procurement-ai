"""Сборка дашборда (один HTML-файл с данными внутри) и архива для отправки."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

from .config import Settings

MARK = "/*__DATA__*/null"


def hide_paths(obj, root: Path, home: Path | None = None):
    """Дашборд публикуется по ссылке: пути внутри проекта — относительные, домашняя папка — «~».
    Так в опубликованной странице не видно имени пользователя Windows или сервера сборки."""
    home = home or Path.home()
    if isinstance(obj, dict):
        return {k: hide_paths(v, root, home) for k, v in obj.items()}
    if isinstance(obj, list):
        return [hide_paths(v, root, home) for v in obj]
    if isinstance(obj, str) and obj:
        for base, label in ((root, None), (home, "~")):
            b = str(base)
            if obj == b or obj.startswith(b.rstrip("\\/") + ("\\" if "\\" in b else "/")):
                rest = obj[len(b):].lstrip("\\/")
                return (str(Path(label) / rest) if rest else label) if label else (rest or ".")
    return obj


def build_dashboard(result: dict, s: Settings) -> Path:
    tpl = (s.root / "dashboard" / "index.html").read_text(encoding="utf-8")
    if MARK not in tpl:
        raise RuntimeError("В шаблоне дашборда нет метки для данных")
    from .control import collect
    from .stoplist import read as stoplist_read
    from .suppliers import read_info, schema
    result = {**result, "control": hide_paths(collect(s, result.get("meta", {}).get("as_of")), s.root),
              "supplier_info": read_info(s), "supplier_schema": schema(), "stoplist": stoplist_read(s)}
    data = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
    data = data.replace("</", "<\\/")  # чтобы текст не закрыл тег <script>
    html = tpl.replace(MARK, data)
    s.docs_dir.mkdir(exist_ok=True)
    out = s.docs_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    (s.docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out


SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "обработано", "dist"}
SKIP_FILES = {".env"}


def package_zip(s: Settings) -> Path:
    dist = s.root / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"procurement-ai_{datetime.now():%Y-%m-%d}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in s.root.rglob("*"):
            rel = p.relative_to(s.root)
            if any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES or p.is_dir():
                continue
            z.write(p, Path("procurement-ai") / rel)
    return out
