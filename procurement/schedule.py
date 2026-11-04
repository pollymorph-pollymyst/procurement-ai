"""Расписание автоматического пересчёта.

На компьютере — задание Планировщика Windows (по дням недели и времени из config.toml).
В облаке — строка cron в .github/workflows/daily.yml (время переводится в UTC).
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys

from .config import ROOT

TASK = "Procurement daily update"
PS_DAYS = {"пн": "Monday", "вт": "Tuesday", "ср": "Wednesday", "чт": "Thursday",
           "пт": "Friday", "сб": "Saturday", "вс": "Sunday"}
CRON_DAYS = {"пн": 1, "вт": 2, "ср": 3, "чт": 4, "пт": 5, "сб": 6, "вс": 0}
WORKFLOW = ROOT / ".github" / "workflows" / "daily.yml"


def _ps(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    enc = base64.b64encode(script.encode("utf-16-le")).decode()  # кириллица в путях не ломается
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                          capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def supported() -> bool:
    return sys.platform == "win32"


def status() -> dict | None:
    """Состояние задания в Планировщике: есть ли, когда следующий запуск. None — не Windows."""
    if not supported():
        return None
    r = _ps(
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        f"$t=Get-ScheduledTask -TaskName {_q(TASK)} -ErrorAction SilentlyContinue;"
        "if(-not $t){'{\"exists\":false}';exit};"
        "$i=$t|Get-ScheduledTaskInfo;"
        "@{exists=$true;state=[string]$t.State;"
        "next=if($i.NextRunTime){$i.NextRunTime.ToString('s')}else{$null};"
        "last=if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){$i.LastRunTime.ToString('s')}else{$null};"
        "result=$i.LastTaskResult}|ConvertTo-Json -Compress")
    try:
        return json.loads(r.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {"exists": False, "error": (r.stderr or r.stdout).strip()[:300]}


def apply(sch: dict) -> dict | None:
    """Создать/обновить или удалить задание Планировщика по расписанию из config.toml."""
    if not supported():
        return None
    if not sch.get("включено"):
        _ps(f"Unregister-ScheduledTask -TaskName {_q(TASK)} -Confirm:$false -ErrorAction SilentlyContinue")
        return status()
    bat = str(ROOT / "scripts" / "run.bat")
    days = ",".join(PS_DAYS[d] for d in sch["дни"])
    r = _ps(
        f"$a=New-ScheduledTaskAction -Execute {_q(bat)} -Argument 'run --no-open --trigger schedule' "
        f"-WorkingDirectory {_q(str(ROOT))};"
        f"$tr=New-ScheduledTaskTrigger -Weekly -DaysOfWeek {days} -At {_q(sch['время'])};"
        "$se=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 1);"
        f"Register-ScheduledTask -TaskName {_q(TASK)} -Action $a -Trigger $tr -Settings $se "
        "-Description 'Пересчёт плана закупок' -Force | Out-Null")
    if r.returncode != 0:
        raise RuntimeError("Не получилось настроить Планировщик Windows: " + (r.stderr or r.stdout).strip()[:300])
    return status()


# ------------------------------------------------------------------ облако (GitHub Actions)

def cron_line(sch: dict) -> str:
    """Время по местному поясу → cron в UTC. Если переход через полночь — сдвигаем и дни."""
    h, m = map(int, sch["время"].split(":"))
    total = h * 60 + m - int(sch.get("часовой_пояс", 3)) * 60
    shift = -1 if total < 0 else (1 if total >= 1440 else 0)
    total %= 1440
    days = sorted({(CRON_DAYS[d] + shift) % 7 for d in sch["дни"]})
    dow = "*" if len(days) == 7 else ",".join(map(str, days))
    return f"{total % 60} {total // 60} * * {dow}"


def write_workflow(sch: dict, path=None) -> bool:
    """Переписать время в строке cron в daily.yml.

    Выключатель относится только к этому компьютеру: выключили здесь (например, потому что считает облако) —
    облачное расписание не трогаем, файл не меняется.
    """
    path = path or WORKFLOW
    if not path.exists() or not sch.get("включено"):
        return False
    text = path.read_text(encoding="utf-8")
    h, m = sch["время"].split(":")
    tz = int(sch.get("часовой_пояс", 3))
    days = ", ".join(sch["дни"]) if len(sch["дни"]) < 7 else "каждый день"
    new = f'  schedule:\n    - cron: "{cron_line(sch)}"   # {h}:{m} (UTC{tz:+d}), {days}\n'
    text2 = re.sub(r"  schedule:\n    - cron: [^\n]*\n", new, text, count=1)
    if text2 == text and "  schedule:" not in text:
        text2 = text.replace("on:\n", "on:\n" + new, 1)
    if text2 != text:
        path.write_text(text2, encoding="utf-8")
    return text2 != text
