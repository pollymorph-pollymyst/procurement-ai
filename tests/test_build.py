from pathlib import Path

from procurement.build import hide_paths


def test_project_paths_become_relative():
    root = Path("C:/Users/someone/procurement-ai")
    out = hide_paths({"inbox": str(root / "Входящие"), "x": [str(root)]}, root, Path("C:/Users/someone"))
    assert out == {"inbox": "Входящие", "x": ["."]}


def test_home_paths_hidden_other_paths_kept():
    root = Path("C:/Users/someone/procurement-ai")
    home = Path("C:/Users/someone")
    out = hide_paths({"a": str(home / "Desktop" / "1С"), "b": "D:\\Выгрузки", "c": "0 4 * * *"}, root, home)
    assert out["a"] == str(Path("~") / "Desktop" / "1С")
    assert "someone" not in out["a"]
    assert out["b"] == "D:\\Выгрузки" and out["c"] == "0 4 * * *"
