# -*- coding: utf-8 -*-
"""automation-notice（自動化提醒視窗）的自測：不開 GUI，純邏輯。

    python selftest.py

檢查四件事：
  1. 描述產生器把每種工具呼叫寫成什麼（畫面上第二行的內容）
  2. 停止鈕的位置與命中判定
  3. 終止 latch 的語意（停了就擋著，只有使用者能解除）
  4. 多 session 的顏色配置

會用到執行期資料夾，跑完自己清乾淨。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import notice as hud

FAILED = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'OK  ' if ok else 'FAIL'} {name}")
    if not ok:
        print(f"       得到 {got!r}\n       預期 {want!r}")
        FAILED.append(name)


def check_that(name: str, ok: bool, note: str = "") -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {name}{'' if ok else '  <- ' + note}")
    if not ok:
        FAILED.append(name)


def describe(tool: str, ti: dict, lang: str = "zh") -> str | None:
    hud._LANG["v"] = lang
    return hud._payload_detail(json.dumps({"tool_name": tool, "tool_input": ti}))[1]


def test_describe() -> None:
    print("\n-- 1. 畫面上會寫什麼 --")
    C = "mcp__claude-in-chrome__"
    check("點擊座標", describe(C + "computer", {"action": "left_click", "coordinate": [832, 614]}),
          "在 Chrome 左鍵點擊 座標 (832, 614)")
    check("輸入文字", describe(C + "computer", {"action": "type", "text": "遊戲企劃"}),
          "在 Chrome 輸入文字：「遊戲企劃」")
    check("按鍵帶次數", describe(C + "computer", {"action": "key", "text": "ctrl+a", "repeat": 2}),
          "在 Chrome 按鍵 ctrl+a ×2")
    check("完整網址", describe(C + "navigate", {"url": "https://a.example.com/b/c?d=1"}),
          "開啟網址 a.example.com/b/c?d=1")
    check_that("未知工具也寫得出參數",
               "payload=你好" in (describe(C + "brand_new_tool_2030", {"payload": "你好"}) or ""),
               "新工具應該走通用 fallback 攤平參數")
    check("純查詢不亮燈", describe(C + "tabs_context_mcp", {}), None)
    check("內建瀏覽器不亮燈", describe("mcp__Claude_Browser__computer", {"action": "left_click"}), None)
    check("無害指令不亮燈", describe("Bash", {"command": "ls -la ~"}), None)
    check_that("前景腳本會亮燈",
               (describe("PowerShell", {"command": "[Windows.Forms.SendKeys]::SendWait('^l')"}) or "")
               .startswith("執行前景鍵鼠腳本"))


def test_english() -> None:
    print("\n-- 1b. 換成英文介面 --")
    C = "mcp__claude-in-chrome__"
    check("英文點擊", describe(C + "computer", {"action": "left_click", "coordinate": [8, 6]}, "en"),
          "Clicking in Chrome at (8, 6)")
    check("英文輸入", describe(C + "computer", {"action": "type", "text": "hi"}, "en"),
          "Typing in Chrome: “hi”")
    check("英文標題", (hud._LANG.update({"v": "en"}), hud.T("title"))[1],
          "Claude is using your computer")
    check_that("英文的未知工具用逗號不用頓號",
               ", " in (describe(C + "zzz", {"a": 1, "b": 2}, "en") or ""))
    check_that("中文的未知工具用頓號",
               "、" in (describe(C + "zzz", {"a": 1, "b": 2}, "zh") or ""))
    hud._LANG["v"] = "zh"


def test_no_buttons() -> None:
    print()
    print('-- 2. 畫面上不該有任何按鈕 --')
    check_that('沒有停止鈕的命中判定', not hasattr(hud, 'hit_stop'),
               '這是提醒視窗不是控制台，停手只走 Ctrl+Alt+Q')
    check_that('沒有停止鈕的位置計算', not hasattr(hud, 'stop_rect'))
    check_that('版面常數不再保留按鈕寬度', not hasattr(hud, 'PANEL_STOP_W'))
    check_that('仍保留列高與表頭高', hud.PANEL_ROW_H > 0 and hud.PANEL_HEADER_H > 0)
    check(chr(31) if False else '第 0 列的垂直中心', hud.row_center_y(0),
          hud.PANEL_PAD_Y + hud.PANEL_HEADER_H + 12)


def test_latch() -> None:
    print("\n-- 3. 停了就擋著（只走熱鍵／CLI）--")
    hud.ensure_overlay("t", "動作 A", ttl=60, pos="bottom", border=True, sid="test_a", label="專案A")
    hud.ensure_overlay("t", "動作 B", ttl=60, pos="bottom", border=True, sid="test_b", label="專案B")
    sids = [r["sid"] for r in hud.active_sessions()]
    check_that("兩條都亮著", "test_a" in sids and "test_b" in sids)

    hud.latch_stop("test_a", "按了提示層上的停止鈕", "動作 A")
    sids = [r["sid"] for r in hud.active_sessions()]
    check_that("被停的那條消失", "test_a" not in sids)
    check_that("沒被停的那條還在", "test_b" in sids)
    check_that("被停的那條有 latch", bool(hud.abort_latched("test_a")))
    check_that("沒被停的那條沒有 latch", not hud.abort_latched("test_b"))

    hud.ensure_overlay("t", "動作 A2", ttl=60, pos="bottom", border=True, sid="test_a", label="專案A")
    check_that("latch 期間就算又寫了狀態，hook 也會擋下來",
               bool(hud.abort_latched("test_a")),
               "latch 不能被 ensure_overlay 洗掉")

    hud.cmd_clear_abort(None)
    check_that("使用者開口後解除", not hud.abort_latched("test_a"))


def test_colors() -> None:
    print("\n-- 4. 多 session 分色 --")
    for f in hud.SESSIONS.glob("*.json"):
        f.unlink()
    got = []
    for i, sid in enumerate(["c1", "c2", "c3"]):
        st = hud.ensure_overlay("t", f"動作{i}", ttl=60, pos="bottom", border=True, sid=sid)
        got.append(st["color"])
    check("三條拿到三個不同顏色", got, [0, 1, 2])
    st = hud.ensure_overlay("t", "再一次", ttl=60, pos="bottom", border=True, sid="c2")
    check("同一條重新亮起沿用原色", st["color"], 1)
    check_that("第一條仍是 Claude 橘", hud.SESSION_COLORS[0] == "#D97757")


def main() -> int:
    hud._LANG["v"] = "zh"      # 斷言寫的是中文，先釘住語言
    # 這是 headless 測試：ensure_overlay() 平常會真的開一個提示層視窗，測試裡每呼叫一次就多開一個
    # （實測一輪留下 7 個 pythonw），所以先把生進程那步換掉，只驗狀態邏輯。
    hud.spawn_overlay = lambda: True
    if hud.overlay_alive():
        print("提示層正在跑，先 `python notice.py stop` 再測（自測會動到執行期狀態）")
        return 2
    keep = hud.APP_DIR.exists()
    try:
        test_describe()
        test_english()
        test_no_buttons()
        test_latch()
        test_colors()
    finally:
        for d in (hud.SESSIONS, hud.ABORT_DIR):
            shutil.rmtree(d, ignore_errors=True)
        if not keep:
            shutil.rmtree(hud.APP_DIR, ignore_errors=True)
    print(f"\n{'全部通過' if not FAILED else str(len(FAILED)) + ' 項失敗：' + '、'.join(FAILED)}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
