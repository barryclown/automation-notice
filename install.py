# -*- coding: utf-8 -*-
"""把 automation-hud 的 hook 接進 Claude Code（~/.claude/settings.json）。

hook 會用「執行這支程式的那個 Python」，所以不用手改任何路徑：

    python install.py              安裝或更新（動 settings.json 之前自動備份）
    python install.py --dry-run    只印出會寫進去的 hook，不動檔案
    python install.py --uninstall  拔掉 automation-hud 的 hook，其他 hook 不動

需求：Windows 10 2004 以上、帶 tkinter 的 Python 3.9+、Claude Code（hook 由 Git Bash 執行）。
資料夾搬家後重跑一次就好，舊路徑的 hook 會被認出來換掉。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HUD = HERE / "hud.py"
# 認出「自己裝過的 hook」：指令裡有 hud.py 接著 hook-pre／hook-prompt／hook-stop。
# 不綁完整路徑，這樣資料夾搬家後重裝也能把舊的清掉。
OURS = re.compile(r"hud\.py'?\s+hook-(?:pre|prompt|stop)\b")


def sh_quote(path) -> str:
    """給 Git Bash 用的單引號字串，路徑一律轉正斜線。"""
    return "'" + str(path).replace("\\", "/").replace("'", "'\\''") + "'"


def build_hooks(python: str, grep_file: Path) -> dict:
    run = f"{sh_quote(python)} -B {sh_quote(HUD)}"
    # Bash／PowerShell 每個指令都會觸發 PreToolUse，所以先用 grep 在 shell 裡過濾，
    # 沒命中關鍵字就不啟動 Python，一般指令才不會被拖慢。
    shell = (f"i=$(cat); printf '%s' \"$i\" | grep -qiF -f {sh_quote(grep_file)} "
             f"&& printf '%s' \"$i\" | {run} hook-pre; exit 0")

    def one(command: str) -> dict:
        return {"type": "command", "command": command, "timeout": 10}

    return {
        "PreToolUse": [
            {"matcher": "mcp__claude-in-chrome__.*", "hooks": [one(f"{run} hook-pre")]},
            {"matcher": "Bash|PowerShell", "hooks": [one(shell)]},
        ],
        "UserPromptSubmit": [{"hooks": [one(f"{run} hook-prompt")]}],
        "Stop": [{"hooks": [one(f"{run} hook-stop")]}],
        "SessionEnd": [{"hooks": [one(f"{run} hook-stop")]}],
    }


def strip_ours(hooks: dict) -> int:
    """把舊的 automation-hud hook 拿掉（原地修改），回傳拿掉幾個。空掉的群組和事件一併收掉。"""
    removed = 0
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            kept = [h for h in g.get("hooks", []) if not OURS.search(str(h.get("command", "")))]
            removed += len(g.get("hooks", [])) - len(kept)
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    return removed


def load_settings(path: Path) -> tuple[dict, str, int, bool]:
    if not path.exists():
        return {}, "\n", 2, True
    raw = path.read_bytes().decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in raw else "\n"
    indent = 2
    for line in raw.splitlines()[1:4]:
        n = len(line) - len(line.lstrip(" "))
        if n:
            indent = n
            break
    return json.loads(raw or "{}"), newline, indent, raw.endswith("\n")


def save_settings(path: Path, data: dict, newline: str, indent: int, trailing: bool) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    if trailing:
        text += "\n"
    if newline != "\n":
        text = text.replace("\n", newline)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-automation-hud")
    tmp.write_bytes(text.encode("utf-8"))
    json.loads(tmp.read_text(encoding="utf-8"))          # 寫出去的東西一定要讀得回來
    os.replace(tmp, path)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="安裝／移除 automation-hud 的 Claude Code hook")
    ap.add_argument("--uninstall", action="store_true", help="只拔掉 automation-hud 的 hook")
    ap.add_argument("--dry-run", action="store_true", help="只印出結果，不寫檔")
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"),
                    help="要改的 settings.json（預設 ~/.claude/settings.json）")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("automation-hud 目前只支援 Windows。")
        return 1
    if not args.uninstall:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            print(f"這個 Python 沒有 tkinter：{sys.executable}\n請換一個有 tkinter 的 Python 來執行 install.py。")
            return 1

    settings = Path(args.settings)
    data, newline, indent, trailing = load_settings(settings)
    hooks = data.setdefault("hooks", {})
    removed = strip_ours(hooks)

    added = 0
    if not args.uninstall:
        sys.path.insert(0, str(HERE))
        sys.dont_write_bytecode = True                       # 別在工具資料夾留 __pycache__
        import hud                                           # 借用同一套路徑與整理邏輯

        local = HERE / "triggers.local.txt"
        if not local.exists() and not args.dry_run:
            local.write_text("# 私人觸發關鍵字（這台機器自己的前景鍵鼠腳本），不進 repo。格式同 triggers.txt。\n",
                             encoding="utf-8")
        grep_file = hud.TRIGGER_GREP if args.dry_run else hud.compile_triggers()
        for event, groups in build_hooks(sys.executable, grep_file).items():
            hooks.setdefault(event, []).extend(groups)
            added += sum(len(g["hooks"]) for g in groups)
    if not hooks:
        del data["hooks"]

    if args.dry_run:
        print(json.dumps({"settings": str(settings), "removed": removed, "added": added,
                          "hooks": build_hooks(sys.executable, Path("<triggers.grep>"))
                          if not args.uninstall else None}, ensure_ascii=False, indent=2))
        return 0

    if settings.exists():
        backup_dir = settings.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{settings.name}.before-automation-hud-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(settings, backup)
        print(f"已備份：{backup}")
    save_settings(settings, data, newline, indent, trailing)
    if args.uninstall:
        print(f"已移除 {removed} 個 automation-hud hook（其他 hook 沒動）。")
    else:
        print(f"已安裝：移除舊的 {removed} 個、寫入 {added} 個 hook，使用 {sys.executable}")
        print(f"觸發關鍵字：{', '.join(hud.load_triggers())}")
        print(f"試亮一次：\"{sys.executable}\" \"{HUD}\" start --detail 測試 --ttl 8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
