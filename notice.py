# -*- coding: utf-8 -*-
"""automation-notice（自動化提醒視窗）— 螢幕上的「Claude 自動化中」提示層。

為什麼需要：AI 在操控使用者已登入的 Chrome／用前景鍵鼠腳本做事時，會把鍵盤與滑鼠
焦點借走。以前只在對話裡打字提醒，使用者在別的視窗根本看不到。這支在螢幕上直接畫
一層「使用中」的提示（四邊橘框 + 底部膠囊條 + 計時），做法對齊 Codex／螢幕錄影軟體
的那種常駐指示器。

特性：
  - 置頂但**永不搶焦點**（WS_EX_NOACTIVATE），也不吃滑鼠點擊（WS_EX_TRANSPARENT）；
    只有按住 Ctrl+Alt 的那幾秒膠囊才收得到滑鼠，好讓使用者把它拖到別的位置
  - 不進工作列、不進 Alt+Tab（WS_EX_TOOLWINDOW）
  - 有 TTL：時間到自動消失，所以不會因為 AI 當掉就永遠卡在畫面上
  - Ctrl+Alt+Q 隨時收回控制權：提示層立刻關閉，並寫下一個**會黏著的終止 latch**——
    在使用者自己開口之前，PreToolUse hook 會直接擋掉所有會借走他鍵鼠的工具呼叫，
    不會再亮第二次（他主動終止就是終止，不該被重試覆蓋）

用法（CLI）：
    python notice.py start  --detail "批次填寫表單" --ttl 600
    python notice.py set    --detail "第 3/15 筆"
    python notice.py status
    python notice.py check-abort          # 使用者按過 Ctrl+Alt+Q 就 exit code 3
    python notice.py clear-abort          # 解除終止 latch（平常交給 hook 自動做）
    python notice.py describe             # 餵一份 hook payload，印出畫面上會顯示什麼
    python notice.py config --opacity 0.5 # 調透明度（即時生效並記住）
    python notice.py config --reset-pos   # 膠囊位置恢復底部置中
    python notice.py config --capture show  # 讓截圖／錄影拍得到提示層（預設 hide＝拍不到）
    python notice.py config --hook-ttl 300  # 兩個自動化動作之間最多隔幾秒還亮著（預設 180）
    python notice.py stop

畫面上的手動操作：
    Ctrl+Alt+Q       立刻收回控制權
    按住 Ctrl+Alt    膠囊暫時變成可抓 -> 直接拖到不擋事的地方（放開就恢復點擊穿透）
    Ctrl+Alt+- / =   當場調透明度；按住 Ctrl+Alt 時滾輪也可以

用法（hook，由 install.py 寫進 ~/.claude/settings.json，讀 stdin 的 hook JSON）：
    python notice.py hook-pre             # PreToolUse：判斷這個工具會不會借走鍵鼠 → 亮起
    python notice.py hook-prompt          # UserPromptSubmit：使用者開口了 → 解除終止 latch
    python notice.py hook-stop            # Stop / SessionEnd：熄掉

內部：
    python notice.py _overlay             # 實際畫面的常駐進程，不要手動叫

執行期檔案放 %LOCALAPPDATA%\\ClaudeAutomationNotice\\：
    state.json      CLI 寫、overlay 讀（標題／說明／到期時間／stop／aborted）
    heartbeat.json  overlay 寫、CLI 讀（pid + 時間戳，用來判斷是不是還活著）
    abort.json      終止 latch，start/stop 都洗不掉，只有 clear-abort 會刪
    config.json     使用者偏好（透明度、膠囊位置、截圖排除、hook_ttl），亮燈不會洗掉
    triggers.grep   triggers.txt＋triggers.local.txt 整理後給 hook 前置過濾用
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 路徑與常數

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ClaudeAutomationNotice"
_OLD_APP_DIR = APP_DIR.with_name("ClaudeAutomationHUD")   # 舊名字（automation-hud 時代）
if _OLD_APP_DIR.exists() and not APP_DIR.exists():
    try:                                   # 改名而已，使用者調好的設定不用重設
        os.replace(_OLD_APP_DIR, APP_DIR)
    except OSError:
        pass

SESSIONS = APP_DIR / "sessions"  # 一個 Claude Code session 一個檔，才能同時顯示好幾條
STATE = SESSIONS / "manual.json"  # 手動 CLI（notice.py start）用的那一條
BEAT = APP_DIR / "heartbeat.json"
ABORT = APP_DIR / "abort.json"   # 使用者主動收回控制權的 latch，只有他下一則訊息會解除
ABORT_DIR = APP_DIR / "aborts"   # 單獨停掉某一條時的 latch（膠囊上的停止鈕）
CONFIG = APP_DIR / "config.json"  # 使用者自己調的外觀偏好（透明度、膠囊放哪），跨次數保留
SELF = Path(__file__).resolve()

BEAT_STALE = 4.0          # 心跳超過這麼久沒更新就當作 overlay 死了
DEFAULT_OPACITY = 0.72    # 預設就偏透明：它是提示層，不該蓋住使用者正在看的東西
MIN_OPACITY = 0.20        # 再低就會看不見，等於沒有提示
MAX_OPACITY = 1.00
DEFAULT_HOOK_TTL = 180    # hook 每亮一次續命幾秒；是「兩個自動化動作之間」最多能隔多久，不是總時長
CONTINUE_WINDOW = 1800    # 同一回合內因為 TTL 熄掉又亮起，計時接著算（回合結束的 stop 會切斷）

# 觸發關鍵字：Bash／PowerShell 指令裡出現就亮燈。每行一個字面關鍵字、不分大小寫。
# triggers.txt 是通用預設（跟著 repo 走），triggers.local.txt 放自己私人的腳本名稱（不進 repo）。
TRIGGER_FILES = (SELF.parent / "triggers.txt", SELF.parent / "triggers.local.txt")
TRIGGER_GREP = APP_DIR / "triggers.grep"   # 兩份合併、去掉空行註解後給 hook 的 grep 用

# 配色（Claude 橘 + 深灰）
TRANS = "#010203"         # 透明色鍵，畫面上不會出現的顏色
ACCENT = "#D97757"
# 同時有好幾個 Claude session 在動你的電腦時，靠顏色分辨是哪一條在做事。
# 第一條維持 Claude 橘（只開一個時畫面不會變），之後依序取用。
SESSION_COLORS = ["#D97757", "#4EA5A0", "#9A7BD0", "#C9A227", "#6E9BD1"]
SESSION_COLORS_DIM = ["#A85536", "#357A76", "#6F58A0", "#95771C", "#4E7099"]
ACCENT_DIM = "#A85536"
PILL_BG = "#1F1E1D"
TEXT_MAIN = "#F5F4EF"
TEXT_SUB = "#B4AFA6"

# ---------------------------------------------------------------- 小工具


def _now() -> float:
    return time.time()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, data: dict) -> None:
    """先寫暫存檔再 os.replace，避免中途炸掉把檔案寫成 0 bytes。

    Windows 上目標檔正被別的進程開著讀的那一瞬間，replace 會丟 PermissionError，
    所以要重試；失敗時也要把自己的暫存檔收掉。以前兩件都沒做，累積了約 70 個
    state.json.tmp<pid> 孤兒檔。hook 進程在寫到一半被 Claude Code 砍掉時連 finally
    都跑不到，那種剩下的交給 sweep_stale_tmp() 定期清。
    """
    path.parent.mkdir(parents=True, exist_ok=True)   # sessions/、aborts/ 都是子資料夾
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def sweep_stale_tmp(max_age: float = 60.0) -> int:
    """清掉執行期資料夾裡超過 max_age 秒的暫存檔（被砍掉的進程留下的），回傳清了幾個。"""
    n = 0
    try:
        for f in APP_DIR.rglob("*.tmp[0-9]*"):   # 子資料夾裡的孤兒也要清
            try:
                if _now() - f.stat().st_mtime > max_age:
                    f.unlink()
                    n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n


def trace(msg: str) -> None:
    """設 NOTICE_DEBUG=1 就把 overlay 的開機階段寫進 boot.log（pythonw 沒 console，只能這樣看）。"""
    # WMI 起的進程拿不到呼叫端的環境變數，所以另外吃一個旗標檔
    if not (os.environ.get("NOTICE_DEBUG") or (APP_DIR / "DEBUG").exists()):
        return
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(APP_DIR / "boot.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid()} {msg}\n")
    except Exception:
        pass


def clamp_opacity(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DEFAULT_OPACITY
    return max(MIN_OPACITY, min(MAX_OPACITY, v))


def read_config() -> dict:
    """使用者偏好：
    - opacity：透明度
    - pill_x／pill_y：膠囊被拖到哪
    - exclude_from_capture：截圖／錄影／直播拍不到提示層（預設開）
    - border：螢幕四邊的橘框（預設開；不想看到提示層就關掉它，再把膠囊推出畫面外）
    - hook_ttl：兩個自動化動作之間最多隔幾秒還維持亮著

    跟 state.json 分開放，因為 state 每次亮燈都會被 ensure_overlay() 整份覆寫
    （同一個坑害過終止 latch），使用者調好的設定不能跟著被洗掉。
    """
    cfg = read_json(CONFIG)
    cfg["opacity"] = clamp_opacity(cfg.get("opacity", DEFAULT_OPACITY))
    cfg["exclude_from_capture"] = bool(cfg.get("exclude_from_capture", True))
    cfg["border"] = bool(cfg.get("border", True))
    try:
        cfg["hook_ttl"] = max(30.0, float(cfg.get("hook_ttl", DEFAULT_HOOK_TTL)))
    except (TypeError, ValueError):
        cfg["hook_ttl"] = float(DEFAULT_HOOK_TTL)
    return cfg


def load_triggers() -> list[str]:
    """讀 triggers.txt＋triggers.local.txt 的關鍵字（字面比對、不分大小寫，不是正規表示式）。

    空行和 # 開頭的註解跳過。用字面比對是因為 hook 前置過濾靠 grep：使用者自己編的檔案
    只要多一個空行就會變成「每個指令都命中」，寫錯一個正規表示式則會讓 grep 整個失敗、
    再也不亮燈。字面關鍵字兩種都不會發生。
    """
    words: list[str] = []
    for f in TRIGGER_FILES:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and line not in words:
                words.append(line)
    return words


def compile_triggers() -> Path:
    """把關鍵字整理成 hook 前置過濾（grep -F -f）直接吃的乾淨清單，來源有變才重寫。"""
    words = load_triggers()
    text = "".join(w + "\n" for w in words)
    try:
        if TRIGGER_GREP.read_text(encoding="utf-8") == text:
            return TRIGGER_GREP
    except OSError:
        pass
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TRIGGER_GREP.with_name(TRIGGER_GREP.name + f".tmp{os.getpid()}")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, TRIGGER_GREP)
    return TRIGGER_GREP


def write_config(cfg: dict) -> None:
    write_json(CONFIG, cfg)


def _safe_sid(sid: str | None) -> str:
    """session id 直接當檔名用，先濾掉路徑字元。"""
    sid = str(sid or "manual")
    keep = [c for c in sid if c.isalnum() or c in "-_"]
    return ("".join(keep) or "manual")[:64]


def session_path(sid: str | None) -> Path:
    return SESSIONS / f"{_safe_sid(sid)}.json"


def active_sessions() -> list[dict]:
    """目前還該亮著的每一條，依開始時間排序（先開的排上面，顏色才不會跳來跳去）。"""
    now = _now()
    out = []
    for f in sorted(SESSIONS.glob("*.json")) if SESSIONS.exists() else []:
        st = read_json(f)
        if not st or st.get("stop") or now > float(st.get("expires_at", 0)):
            continue
        st["sid"] = f.stem
        out.append(st)
    out.sort(key=lambda d: float(d.get("started_at", 0)))
    return out


def _free_color_idx(sid: str) -> int:
    """挑一個目前沒人用的顏色；同一條 session 重新亮起時沿用原本的。"""
    used = set()
    for st in active_sessions():
        if st.get("sid") == sid:
            return int(st.get("color", 0))
        used.add(int(st.get("color", 0)))
    for i in range(len(SESSION_COLORS)):
        if i not in used:
            return i
    return len(used) % len(SESSION_COLORS)


def abort_latched(sid: str | None = None) -> dict:
    """使用者按過 Ctrl+Alt+Q 就會留下這個檔；在他解除前，任何借鍵鼠的動作都不該再來第二次。"""
    latch = read_json(ABORT)
    if latch:
        return latch
    if sid:
        one = read_json(ABORT_DIR / f"{_safe_sid(sid)}.json")
        if one:
            return one
    return {}


def overlay_alive() -> bool:
    beat = read_json(BEAT)
    return bool(beat) and (_now() - float(beat.get("ts", 0))) < BEAT_STALE


# 膠囊版面常數。抽到模組層是為了可測：停止鈕的位置與命中判定不必開 GUI 就能驗。
PANEL_PAD_X, PANEL_PAD_Y = 18, 10
PANEL_HEADER_H, PANEL_ROW_H = 22, 24
# 不想看到它時可以把膠囊推出畫面外，但一定要留這麼寬的一截在畫面裡當把手，
# 不然推出去就再也抓不回來了（真的想整條消失就 config --border off 再推出去，
# 或者 config --reset-pos 把它叫回來）。
PANEL_HANDLE = 28


def clamp_pill(x: int, y: int, pw: int, ph: int, area: tuple) -> tuple[int, int]:
    """把膠囊夾在「至少露出 PANEL_HANDLE」的範圍內，而不是整個框都要在畫面裡。"""
    wx, wy, ww, wh = area
    x = max(wx - pw + PANEL_HANDLE, min(int(x), wx + ww - PANEL_HANDLE))
    y = max(wy - ph + PANEL_HANDLE, min(int(y), wy + wh - PANEL_HANDLE))
    return x, y


def row_center_y(i: int) -> int:
    return PANEL_PAD_Y + PANEL_HEADER_H + PANEL_ROW_H * i + 12


# ---------------------------------------------------------------- 文案（中／英）
# 介面文字與動作描述都走這張表。預設看 Windows 的顯示語言自動選，
# `notice.py config --lang en|zh` 可以指定。
# UI strings and action descriptions all go through this table. The language
# follows the Windows display language by default; `notice.py config --lang` overrides it.

STRINGS = {
    "zh": {
        "title": "Claude 自動化中",
        "hint": "Ctrl+Alt+Q 收回控制權",
        "where_coord": "座標 ({x}, {y})",
        "where_ref": "元素 {ref}",
        "where_page": "頁面",
        "no_args": "（無參數）",
        "c_type": "在 Chrome 輸入文字：「{text}」",
        "c_key": "在 Chrome 按鍵 {keys}",
        "c_key_rep": "在 Chrome 按鍵 {keys} ×{n}",
        "c_click": "在 Chrome {act} {where}",
        "c_click_mod": "在 Chrome {act} {where}（按住 {mod}）",
        "c_scroll": "在 Chrome 捲動 {dir} {amount} 格於 {where}",
        "c_drag": "在 Chrome 拖曳 {a} → {b}",
        "c_wait": "在 Chrome 等待 {sec} 秒",
        "c_scroll_to": "在 Chrome 捲到 {where}",
        "c_other": "在 Chrome {act}",
        "navigate": "開啟網址 {url}",
        "form_input": "填入表單 {ref}：「{value}」",
        "file_upload": "上傳檔案 {path}",
        "upload_image": "上傳圖片到頁面",
        "javascript": "在頁面執行腳本：{code}",
        "find": "在頁面搜尋「{query}」",
        "read_page": "讀取頁面內容",
        "read_console": "讀取頁面 console 訊息",
        "read_network": "讀取頁面網路請求",
        "tab_new": "開一個新分頁",
        "tab_close": "關閉分頁 {tab}",
        "shortcut": "執行快捷操作 {name}",
        "resize": "調整瀏覽器視窗為 {size}",
        "gif": "錄製畫面 GIF",
        "switch_browser": "切換到瀏覽器 {id}",
        "batch": "連續操作 Chrome {n} 步：{steps}",
        "batch_plain": "連續操作 Chrome",
        "unknown": "Chrome {tool}：{args}",
        "shell": "執行前景鍵鼠腳本 {what}：{cmd}",
        "shell_generic": "前景腳本",
        "act_left_click": "左鍵點擊", "act_right_click": "右鍵點擊",
        "act_double_click": "雙擊", "act_triple_click": "三擊",
        "act_type": "輸入文字", "act_key": "按鍵", "act_scroll": "捲動",
        "act_screenshot": "截圖", "act_wait": "等待", "act_hover": "游標移到",
        "act_left_click_drag": "拖曳", "act_zoom": "放大檢視", "act_scroll_to": "捲到元素",
        "deny": ("使用者在 {when} {how}主動收回鍵盤與電腦控制權（當時正在：{detail}）。"
                 "在他自己開口之前，不要再嘗試任何會借走他鍵鼠的操作；請直接停手並回報進度。"),
        "how_hotkey": "按了 Ctrl+Alt+Q ",
        "how_button": "按了提示層上的停止鈕 ",
        "cli_lit": "提示層已亮起",
        "cli_lit_fail": "提示層啟動失敗（狀態已寫入）",
        "cli_updated": "提示層已更新：{detail}",
        "cli_off": "提示層已熄滅",
        "cli_aborted": "已被主動終止過",
        "cli_abort_msg": ("已停止（{when}）：使用者{how}收回控制權，立刻停手，"
                          "在他開口之前不要再自動化。中斷時正在做：{detail}"),
        "cli_ok": "OK",
        "cli_cleared": "已解除終止狀態",
        "cli_not_latched": "本來就沒有終止狀態",
        "cli_triggers": "觸發關鍵字已更新",
        "cli_no_light": "(不亮燈)",
    },
    "en": {
        "title": "Claude is using your computer",
        "hint": "Ctrl+Alt+Q to take back control",
        "where_coord": "at ({x}, {y})",
        "where_ref": "on {ref}",
        "where_page": "on the page",
        "no_args": "(no arguments)",
        "c_type": "Typing in Chrome: “{text}”",
        "c_key": "Pressing {keys} in Chrome",
        "c_key_rep": "Pressing {keys} in Chrome ×{n}",
        "c_click": "{act} in Chrome {where}",
        "c_click_mod": "{act} in Chrome {where} (holding {mod})",
        "c_scroll": "Scrolling {dir} {amount} in Chrome {where}",
        "c_drag": "Dragging in Chrome {a} → {b}",
        "c_wait": "Waiting {sec}s in Chrome",
        "c_scroll_to": "Scrolling to {where} in Chrome",
        "c_other": "{act} in Chrome",
        "navigate": "Opening {url}",
        "form_input": "Filling {ref}: “{value}”",
        "file_upload": "Uploading {path}",
        "upload_image": "Uploading an image to the page",
        "javascript": "Running script on the page: {code}",
        "find": "Searching the page for “{query}”",
        "read_page": "Reading the page",
        "read_console": "Reading the page console",
        "read_network": "Reading the page network requests",
        "tab_new": "Opening a new tab",
        "tab_close": "Closing tab {tab}",
        "shortcut": "Running shortcut {name}",
        "resize": "Resizing the browser to {size}",
        "gif": "Recording a GIF",
        "switch_browser": "Switching to browser {id}",
        "batch": "{n} Chrome actions in a row: {steps}",
        "batch_plain": "A batch of Chrome actions",
        "unknown": "Chrome {tool}: {args}",
        "shell": "Running foreground input script {what}: {cmd}",
        "shell_generic": "foreground script",
        "act_left_click": "Clicking", "act_right_click": "Right-clicking",
        "act_double_click": "Double-clicking", "act_triple_click": "Triple-clicking",
        "act_type": "Typing", "act_key": "Pressing keys", "act_scroll": "Scrolling",
        "act_screenshot": "Taking a screenshot", "act_wait": "Waiting",
        "act_hover": "Hovering", "act_left_click_drag": "Dragging",
        "act_zoom": "Zooming", "act_scroll_to": "Scrolling to element",
        "deny": ("The user took back control of the keyboard and computer at {when} "
                 "({how}; you were: {detail}). Do not attempt anything that borrows "
                 "their keyboard, mouse or browser again until they speak up. "
                 "Stop now and report where you got to."),
        "how_hotkey": "pressed Ctrl+Alt+Q",
        "how_button": "clicked the stop button on the overlay",
        "cli_lit": "Overlay is up",
        "cli_lit_fail": "Overlay failed to start (state written)",
        "cli_updated": "Overlay updated: {detail}",
        "cli_off": "Overlay is off",
        "cli_aborted": " (was stopped by the user)",
        "cli_abort_msg": ("STOPPED ({when}): the user {how} and took back control. "
                          "Stop now and do not automate anything until they speak up. "
                          "You were: {detail}"),
        "cli_ok": "OK",
        "cli_cleared": "Stop latch cleared",
        "cli_not_latched": "No stop latch was set",
        "cli_triggers": "Trigger list rebuilt",
        "cli_no_light": "(no overlay)",
    },
}

_LANG = {"v": None}


def ui_lang() -> str:
    """zh 或 en。設定檔優先，否則看 Windows 的顯示語言。"""
    if _LANG["v"]:
        return _LANG["v"]
    want = str(read_json(CONFIG).get("lang") or "auto").lower()
    if want in ("zh", "en"):
        _LANG["v"] = want
        return want
    try:                                   # 0x04 = Chinese（正體／簡體都算）
        prim = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        _LANG["v"] = "zh" if prim == 0x04 else "en"
    except Exception:
        _LANG["v"] = "en"
    return _LANG["v"]


def T(key: str, **kw) -> str:
    s = STRINGS.get(ui_lang(), STRINGS["en"]).get(key) or STRINGS["en"].get(key, key)
    return s.format(**kw) if kw else s


def fmt_elapsed(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- 啟動 overlay


def _pythonw() -> str:
    cand = Path(sys.executable).with_name("pythonw.exe")
    return str(cand if cand.exists() else sys.executable)


def spawn_overlay() -> bool:
    """把 overlay 起成一個活得比這次工具呼叫久的進程。

    agent 的 shell 活在 KILL_ON_JOB_CLOSE 的 job 裡，一般子進程會跟著那一次呼叫被殺。
    實測這個 job 允許 breakaway，所以 CREATE_BREAKAWAY_FROM_JOB 就夠了，而且比 WMI 可靠
    （WMI 建出來的進程在這台機器上會靜默死掉，連 hud.py 的第一行都沒跑到）。
    """
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    argv = [_pythonw(), "-B", str(SELF), "_overlay"]
    for flags in (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
                  DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP):
        try:
            subprocess.Popen(argv, creationflags=flags, cwd=str(SELF.parent))
            return True
        except OSError:
            continue
    # 最後退路：WMI（不在同一個 job，但這台機器上不穩）
    try:
        cmd = f'"{_pythonw()}" -B "{SELF}" _overlay'.replace("'", "''")
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
             f"-Arguments @{{CommandLine='{cmd}'}} | Out-Null"],
            capture_output=True, timeout=25)
        return True
    except Exception:
        return False


def ensure_overlay(title: str, detail: str, ttl: float, pos: str, border: bool,
                   sid: str | None = None, label: str = "") -> dict:
    """寫這一條 session 的狀態，並確保 overlay 活著（已經活著就只更新，不會開第二個）。"""
    alive = overlay_alive()
    path = session_path(sid)
    prev = read_json(path)
    old = prev if alive else {}
    now = _now()
    # TTL 只管「兩個動作之間」隔多久；同一回合裡 AI 想比較久、TTL 到了熄掉又亮起來時，
    # 計時要接著算，不能歸零。回合結束（Stop／使用者開口）會寫 stop=True，那才重新計時。
    continuing = (
        bool(prev) and not prev.get("stop") and not prev.get("aborted")
        and now - float(prev.get("updated_at", 0)) < CONTINUE_WINDOW)
    state = {
        "title": title or old.get("title") or T("title"),
        "detail": detail if detail is not None else old.get("detail", ""),
        "label": label or old.get("label") or prev.get("label", ""),
        "color": int(prev["color"]) if continuing and "color" in prev
                 else _free_color_idx(_safe_sid(sid)),
        "started_at": float(prev.get("started_at", now)) if continuing else now,
        "expires_at": now + float(ttl),
        "pos": pos or old.get("pos", "bottom"),
        "border": border if border is not None else old.get("border", True),
        "stop": False,
        "aborted": False,
        "updated_at": now,
    }
    if not alive:
        # overlay 還沒起來前的前景才一定是使用者的視窗，交給 overlay 當還焦點的目標
        try:
            state["restore_fg"] = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            pass
    write_json(path, state)
    if not alive:
        write_json(BEAT, {"ts": 0, "pid": 0})
        spawn_overlay()
    return state


def latch_stop(sid: str, how: str, detail: str = "") -> None:
    """收回某一條 session 的控制權：寫黏著 latch ＋ 收掉那一列。

    latch 跟 Ctrl+Alt+Q 同語意——在使用者自己開口之前，那條 session 的 PreToolUse
    都會被擋掉，不會自己再亮第二次。
    """
    write_json(ABORT_DIR / f"{_safe_sid(sid)}.json",
               {"at": _now(), "detail": detail, "how": how, "sid": sid})
    stop_session(sid)


def stop_session(sid: str | None) -> None:
    """把某一條標成結束（overlay 下一個 tick 就會把那條收掉）。"""
    path = session_path(sid)
    st = read_json(path)
    if st and not st.get("stop"):
        st.update({"stop": True, "updated_at": _now()})
        write_json(path, st)


# ---------------------------------------------------------------- Win32


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def work_area() -> tuple[int, int, int, int]:
    """回傳桌面工作區 (x, y, w, h)，扣掉工作列，這樣四邊框都看得到。"""
    r = RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    if not ok:
        u = ctypes.windll.user32
        return 0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def make_click_through(hwnd: int, click_through: bool = True) -> None:
    """套上「不搶焦點、不進工作列」的樣式；click_through=False 會讓它吃得到滑鼠。

    膠囊要能被拖走就得暫時收掉 WS_EX_TRANSPARENT，其餘旗標一律留著——沒有
    NOACTIVATE 的話，使用者抓著膠囊拖一下就把焦點從他正在打字的視窗搶走了。
    """
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    u = ctypes.windll.user32
    u.GetWindowLongW.restype = ctypes.c_long
    ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if click_through:
        ex |= WS_EX_TRANSPARENT
    else:
        ex &= ~WS_EX_TRANSPARENT
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def is_cloaked(h: int) -> bool:
    """DWM cloak：UWP 的隱形殘影、其他虛擬桌面上的視窗都是 cloaked，焦點給它們等於沒給。"""
    cloaked = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(h), 14, ctypes.byref(cloaked), 4)
    except Exception:
        return False
    return bool(cloaked.value)


def usable_focus_target(h: int, mine=()) -> bool:
    """h 能不能接住還回去的焦點。SetForegroundWindow 對 cloaked／隱形視窗會回報成功，
    但前景其實還停在 HUD 上（2026-09-24 實測卡了 17 秒），所以還之前要先過濾。"""
    u = ctypes.windll.user32
    h = int(h or 0)
    return bool(h and h not in mine and u.IsWindow(h) and u.IsWindowVisible(h)
                and not u.IsIconic(h) and not is_cloaked(h))


def describe_hwnd(h: int) -> str:
    """trace 用：0x1234:類別:標題，事後才看得出焦點被丟到哪裡。"""
    u = ctypes.windll.user32
    h = int(h or 0)
    if not h:
        return "0"
    cls = ctypes.create_unicode_buffer(64)
    title = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(h, cls, 64)
    u.GetWindowTextW(h, title, 64)
    return f"{h:#x}:{cls.value}:{title.value[:24]}"


def next_activatable_window(skip) -> int:
    """挑 Z 序中最上面那個「真的能用」的視窗，當作把焦點還回去的對象。

    需要它是因為：原本的前景視窗如果被關掉（例如一個 Chrome 對話框），Windows 會把
    置頂的 HUD 選成新前景，而我們記下的還原目標已經是死 handle。
    skip 可以是單一 handle 或一組（HUD 自己的兩個視窗都要跳過）。
    """
    u = ctypes.windll.user32
    skips = set(skip) if isinstance(skip, (tuple, list, set)) else {skip}
    found: list[int] = []

    def cb(h, _l):
        h = int(h or 0)
        if not h or h in skips:
            return True
        if not u.IsWindowVisible(h) or u.IsIconic(h):
            return True
        # WS_EX_TOOLWINDOW | WS_EX_TOPMOST：置頂的多半是系統彈窗（「快顯主機」之類），
        # 焦點丟給它等於沒還給使用者（2026-09-24 實測挑錯過一次）
        if u.GetWindowLongW(h, -20) & (0x00000080 | 0x00000008):
            return True
        if u.GetWindowTextLengthW(h) <= 0:
            return True
        if is_cloaked(h):
            return True
        found.append(h)
        return False

    try:
        u.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        return 0
    return found[0] if found else 0


def force_foreground(hwnd: int) -> bool:
    """把焦點推給 hwnd；被前景鎖擋住時用送一次 ALT 的老招解鎖。"""
    u = ctypes.windll.user32
    if u.SetForegroundWindow(hwnd):
        return True
    try:
        u.keybd_event(0x12, 0, 0, 0)               # ALT down
        u.keybd_event(0x12, 0, 2, 0)               # ALT up
    except Exception:
        pass
    return bool(u.SetForegroundWindow(hwnd))


def set_capture_excluded(hwnd: int, excluded: bool) -> bool:
    """WDA_EXCLUDEFROMCAPTURE：螢幕截圖、錄影、直播軟體都拍不到這個視窗，人眼照樣看得到。

    用意是讓 AI 自己的截圖不會被提示層擋住，直播／錄影時也不會把「AI 正在輸入的內容」
    拍出去。需要 Windows 10 2004 以上；舊版回傳 False，提示層照常顯示。
    """
    WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x00, 0x11
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE))
    except Exception:
        return False


def keep_topmost(hwnd: int) -> None:
    HWND_TOPMOST = -1
    SWP = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
    ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP)


def toplevel_hwnd(root) -> int:
    hwnd = root.winfo_id()
    parent = ctypes.windll.user32.GetParent(hwnd)
    return parent if parent else hwnd


# ---------------------------------------------------------------- overlay 本體


def run_overlay() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    import threading
    import tkinter as tk
    from tkinter import font as tkfont

    trace(f"boot appdir={APP_DIR} sessions={len(active_sessions())}")
    rows0 = active_sessions()
    if not rows0:
        trace("early-exit: 沒有活著的 session")
        return
    state = rows0[0]
    trace("state ok")
    trace(f"swept stale tmp={sweep_stale_tmp()}")

    cfg = read_config()
    opa = {"v": clamp_opacity(cfg.get("opacity")), "dirty": False, "wrote_at": 0.0}

    wx, wy, ww, wh = work_area()
    trace(f"workarea={wx},{wy},{ww}x{wh} opacity={opa['v']}")

    _u = ctypes.windll.user32

    # Tk 建立程式的第一個視窗外框時會主動 SetActiveWindow（tkWinWm.c UpdateWrapper 的
    # firstWindow 分支），而剛啟動的進程在 Windows 眼中有搶前景的權利 → 還沒顯示的 HUD
    # 就在第一次 update_idletasks 拿走前景（2026-09-24 逐步量前景確認；拿掉 -topmost 沒有用）。
    # 趁還有權利時先把前景鎖上，Tk 的啟用就搶不到前景；show_windows 之後再解鎖。
    fg_locked = bool(_u.LockSetForegroundWindow(1))    # LSFW_LOCK
    trace(f"LockSetForegroundWindow={fg_locked}")
    # 邊框那層：整片蓋住工作區，永遠點擊穿透。
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.configure(bg=TRANS)
    root.attributes("-topmost", True)
    root.attributes("-alpha", opa["v"])
    root.attributes("-transparentcolor", TRANS)
    root.geometry(f"{ww}x{wh}+{wx}+{wy}")

    cv = tk.Canvas(root, width=ww, height=wh, bg=TRANS, highlightthickness=0, bd=0)
    cv.pack(fill="both", expand=True)

    # 膠囊獨立成第二個視窗，才能被拖著走（整片視窗沒辦法只讓中間那塊可拖）。
    pill = tk.Toplevel(root)
    pill.withdraw()
    pill.overrideredirect(True)
    pill.configure(bg=TRANS)
    pill.attributes("-topmost", True)
    pill.attributes("-alpha", opa["v"])
    pill.attributes("-transparentcolor", TRANS)
    pcv = tk.Canvas(pill, bg=TRANS, highlightthickness=0, bd=0)
    pcv.pack(fill="both", expand=True)

    # 先建立視窗但不顯示 -> 套好 NOACTIVATE 再顯示，避免搶走使用者的焦點
    root.update_idletasks()
    hwnd = toplevel_hwnd(root)
    hwnd_pill = toplevel_hwnd(pill)
    trace(f"hwnd={hwnd} pill={hwnd_pill}")
    make_click_through(hwnd)
    make_click_through(hwnd_pill)

    u = ctypes.windll.user32
    mine = (hwnd, hwnd_pill)
    # 還原目標優先用 hook-pre 在啟動 overlay 之前記下的前景：overlay 自己開機時量到的
    # 前景可能已經是自己（使用者一陣子沒按鍵、前景鎖過期時，新進程會直接拿到前景）。
    first = 0
    for r in rows0:                       # 誰記下了使用者原本的前景就用誰的
        if int(r.get("restore_fg") or 0):
            first = int(r["restore_fg"])
            break
    if not usable_focus_target(first, mine):
        first = u.GetForegroundWindow()
    fg_guard = {"last": first}
    trace(f"restore target={describe_hwnd(first)}")

    # Tk 一進 mainloop 就會把主視窗拉到前景（實測 ex-style 正確也擋不住），
    # 所以額外掛一道守衛：只要前景變成我們自己，就立刻還給使用者原本那個視窗。
    def guard(fast_left: int = 0) -> None:
        cur = u.GetForegroundWindow()
        if cur in mine:
            target = fg_guard["last"]
            if not usable_focus_target(target, mine):
                target = next_activatable_window(mine)   # 記的那個不能用就重挑一個
            ok = bool(target) and force_foreground(target)
            # SetForegroundWindow 回報成功不代表焦點真的過去了，還停在自己身上就換下一個
            if u.GetForegroundWindow() in mine:
                alt = next_activatable_window(mine)
                if alt and alt != target:
                    target, ok = alt, force_foreground(alt)
            for h in mine:
                u.ShowWindow(h, 4)                       # SW_SHOWNOACTIVATE
            if fg_guard.get("logged", 0) < 6:
                fg_guard["logged"] = fg_guard.get("logged", 0) + 1
                trace(f"guard: 搶回焦點 target={describe_hwnd(target)} ok={ok} "
                      f"after={describe_hwnd(u.GetForegroundWindow())}")
        elif cur:
            fg_guard["last"] = cur
        if fast_left > 0:
            root.after(25, guard, fast_left - 1)

    def show_windows() -> None:
        """版面排好之後才顯示。

        先用 SW_SHOWNOACTIVATE 顯示、之後才 pill.geometry() 的話，Tk 在 idle 套用版面時會把
        膠囊啟用，搶走前景約 0.5 秒（2026-09-24 實測）。所以先在隱藏狀態 update_idletasks 把
        位置尺寸全部套好，再顯示。也不要用 deiconify：它走 ShowWindow(SW_NORMAL)，即使掛了
        WS_EX_NOACTIVATE 還是會搶焦點（實測過）。
        """
        root.update_idletasks()
        prev_fg = u.GetForegroundWindow()
        if usable_focus_target(prev_fg, mine):
            fg_guard["last"] = prev_fg
        trace(f"show: prev_fg={describe_hwnd(prev_fg)}")
        excl = cfg.get("exclude_from_capture", True)
        trace(f"exclude_from_capture={excl} ok={[set_capture_excluded(h, excl) for h in mine]}")
        for h in mine:
            u.ShowWindow(h, 4)  # SW_SHOWNOACTIVATE
            make_click_through(h)
            keep_topmost(h)
        # 啟動視窗的 activate 是非同步的，補一段時間盯著。只在前景變成 HUD 自己時才還，
        # 使用者這段時間自己切到別的視窗就尊重他，不能把他拉回去。
        for _ in range(12):
            if u.GetForegroundWindow() in mine:
                guard()
            time.sleep(0.04)
        trace(f"shown, fg={describe_hwnd(u.GetForegroundWindow())} "
              f"hud_has_fg={u.GetForegroundWindow() in mine}")

    f_bold = tkfont.Font(family="Microsoft JhengHei UI", size=11, weight="bold")
    f_norm = tkfont.Font(family="Microsoft JhengHei UI", size=10)

    flags = {"abort": False, "quit": False}

    def hotkey_watch() -> None:
        u = ctypes.windll.user32
        while not flags["quit"]:
            try:
                if (u.GetAsyncKeyState(0x11) & 0x8000 and      # Ctrl
                        u.GetAsyncKeyState(0x12) & 0x8000 and  # Alt
                        u.GetAsyncKeyState(0x51) & 0x8000):    # Q
                    flags["abort"] = True
                    return
            except Exception:
                return
            time.sleep(0.05)

    threading.Thread(target=hotkey_watch, daemon=True).start()

    def round_rect(canvas, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return canvas.create_polygon(pts, smooth=True, **kw)

    def ellipsize(text: str, font, limit: int) -> str:
        if font.measure(text) <= limit:
            return text
        while text and font.measure(text + "\u2026") > limit:
            text = text[:-1]
        return text + "\u2026"

    # 版面：表頭一行（狀態＋熱鍵）＋ 每個 session 一行（顏色點／專案名／動作／計時／停止鈕）
    PAD_X, PAD_Y = PANEL_PAD_X, PANEL_PAD_Y
    HEADER_H, ROW_H = PANEL_HEADER_H, PANEL_ROW_H
    layout: dict = {"key": None, "size": (0, 0), "rows": [], "header_dot": None}
    pillpos: dict = {"x": None, "y": None}
    flash: dict = {}          # sid -> 這一步是什麼時候換的，用來閃一下
    # 這是提醒視窗，不是控制台：畫面上沒有任何按鈕，停手一律用 Ctrl+Alt+Q。
    # 所以它永遠點擊穿透，只有「按住 Ctrl+Alt」時才暫時吃得到滑鼠好讓人把它拖走——
    # 自動化期間滑鼠會在整個螢幕亂點，只要視窗吃得到點擊，剛好落在它上面的那一下
    # 就會被吞掉，被操控的程式收不到，自動化會莫名其妙失敗。
    grab = {"on": False, "dragging": False, "dx": 0, "dy": 0}

    def row_color(st: dict, dim: bool = False) -> str:
        i = int(st.get("color", 0)) % len(SESSION_COLORS)
        return (SESSION_COLORS_DIM if dim else SESSION_COLORS)[i]

    def place_pill(pw: int, ph: int, st: dict) -> None:
        """決定膠囊放哪：拖過就用拖到的位置，沒拖過就照 pos 貼上下緣置中。"""
        x, y = pillpos["x"], pillpos["y"]
        if x is None or y is None:
            c = read_config()
            if c.get("pill_x") is not None and c.get("pill_y") is not None:
                x, y = c["pill_x"], c["pill_y"]
            else:
                x = wx + (ww - pw) // 2
                y = wy + (16 if st.get("pos") == "top" else wh - ph - 16)
        # 允許推出畫面外，但至少留一截抓得到；換過解析度的舊座標也靠它拉回來
        x, y = clamp_pill(x, y, pw, ph, (wx, wy, ww, wh))
        pillpos["x"], pillpos["y"] = x, y
        pill.geometry(f"{pw}x{ph}+{x}+{y}")

    def draw_panel(rows: list) -> None:
        """一個 session 一行。只開一個 Claude 時看起來跟以前一樣，開很多個才會長高。"""
        key = tuple((r["sid"], r.get("label"), r.get("detail")) for r in rows)
        key += (rows[0].get("pos") if rows else None,)
        if key == layout["key"]:
            return
        prev_detail = {r["sid"]: r.get("detail") for r in layout["rows"]}
        layout["key"] = key
        pcv.delete("pill")
        layout["rows"] = []

        w_time = f_norm.measure("88:88")
        w_head = 16 + f_bold.measure(T("title")) + 12 + f_norm.measure(T("hint"))
        budget = int(ww * 0.9) - PAD_X * 2
        cells = []
        for r in rows:
            label = r.get("label") or ""
            detail = ellipsize(r.get("detail") or "", f_norm,
                               budget - 16 - f_bold.measure(label) - 12 - w_time - 12)
            cells.append((label, detail))
        w_rows = max((16 + f_bold.measure(lb) + (10 if lb else 0) + f_norm.measure(dt)
                      + 12 + w_time for lb, dt in cells), default=0)

        pw = PAD_X * 2 + max(w_head, w_rows)
        ph = PAD_Y * 2 + HEADER_H + ROW_H * len(rows)
        if layout["size"] != (pw, ph):
            layout["size"] = (pw, ph)
            pcv.config(width=pw, height=ph)
        place_pill(pw, ph, rows[0] if rows else {})

        round_rect(pcv, 1, 1, pw - 1, ph - 1, 14, fill=PILL_BG,
                   outline=TEXT_MAIN if grab["on"] else ACCENT, width=1,
                   tags=("pill", "pillframe"))

        # 表頭
        cy = PAD_Y + 11
        layout["header_dot"] = pcv.create_oval(PAD_X, cy - 5, PAD_X + 10, cy + 5,
                                               fill=ACCENT, outline="", tags="pill")
        x = PAD_X + 16
        pcv.create_text(x, cy, text=T("title"), anchor="w", font=f_bold,
                        fill=TEXT_MAIN, tags="pill")
        pcv.create_text(pw - PAD_X, cy, text=T("hint"), anchor="e", font=f_norm,
                        fill=TEXT_SUB, tags="pill")

        # 每個 session 一行
        for i, (r, (label, detail)) in enumerate(zip(rows, cells)):
            sid = r["sid"]
            cy = row_center_y(i)
            col = row_color(r)
            if prev_detail.get(sid) not in (None, r.get("detail")):
                flash[sid] = _now()          # 換了動作就閃一下，才看得出它有在動
            x = PAD_X
            dot = pcv.create_oval(x, cy - 4, x + 8, cy + 4, fill=col, outline="",
                                  tags="pill")
            x += 16
            if label:
                pcv.create_text(x, cy, text=label, anchor="w", font=f_bold, fill=col,
                                tags="pill")
                x += f_bold.measure(label) + 10
            pcv.create_text(x, cy, text=detail, anchor="w", font=f_norm,
                            fill=TEXT_SUB, tags="pill")
            tid = pcv.create_text(pw - PAD_X, cy, text="00:00", anchor="e",
                                  font=f_norm, fill=col, tags="pill")
            layout["rows"].append({"sid": sid, "dot_id": dot, "time_id": tid,
                                   "color": col, "dim": row_color(r, True),
                                   "detail": r.get("detail"),
                                   "started_at": float(r.get("started_at", _now()))})

    def draw_border(color: str) -> None:
        cv.delete("border")
        t = 4
        for x1, y1, x2, y2 in ((0, 0, ww, t), (0, wh - t, ww, wh),
                               (0, 0, t, wh), (ww - t, 0, ww, wh)):
            cv.create_rectangle(x1, y1, x2, y2, fill=color, outline="", tags="border")

    def set_grab(on: bool) -> None:
        if grab["on"] == on:
            return
        grab["on"] = on
        make_click_through(hwnd_pill, click_through=not on)
        keep_topmost(hwnd_pill)
        try:
            pill.config(cursor="hand2" if on else "")
            pcv.itemconfig("pillframe", outline=TEXT_MAIN if on else ACCENT)
        except Exception:
            pass

    def on_press(e) -> None:
        grab["dragging"] = True
        grab["dx"] = e.x_root - pill.winfo_x()
        grab["dy"] = e.y_root - pill.winfo_y()

    def on_motion(e) -> None:
        if not grab["dragging"]:
            return
        pw, ph = layout["size"]
        x, y = clamp_pill(e.x_root - grab["dx"], e.y_root - grab["dy"], pw, ph,
                          (wx, wy, ww, wh))
        pillpos["x"], pillpos["y"] = x, y
        pill.geometry(f"+{x}+{y}")

    def on_release(_e) -> None:
        if not grab["dragging"]:
            return
        grab["dragging"] = False
        c = read_config()
        c["pill_x"], c["pill_y"] = pillpos["x"], pillpos["y"]
        write_config(c)
        cfg_seen["mtime"] = _cfg_mtime()

    def on_wheel(e) -> None:
        # 抓著的時候滾輪直接調透明度，比記熱鍵直覺
        bump_opacity(0.04 if e.delta > 0 else -0.04)

    pill.bind("<ButtonPress-1>", on_press)
    pill.bind("<B1-Motion>", on_motion)
    pill.bind("<ButtonRelease-1>", on_release)
    pill.bind("<MouseWheel>", on_wheel)

    def apply_opacity(v: float) -> None:
        opa["v"] = v
        root.attributes("-alpha", v)
        pill.attributes("-alpha", v)

    def bump_opacity(delta: float) -> None:
        v = clamp_opacity(opa["v"] + delta)
        if abs(v - opa["v"]) < 1e-6:
            return
        apply_opacity(v)
        opa["dirty"] = True

    def _cfg_mtime() -> float:
        try:
            return CONFIG.stat().st_mtime
        except OSError:
            return 0.0

    cfg_seen = {"mtime": _cfg_mtime()}

    def input_poll() -> None:
        """80ms 輪詢 Ctrl+Alt：按著就讓膠囊可抓，同時 -/+ 連續調透明度。"""
        if flags["quit"]:
            return
        holding = bool(u.GetAsyncKeyState(0x11) & 0x8000
                       and u.GetAsyncKeyState(0x12) & 0x8000)

        if holding:
            if u.GetAsyncKeyState(0xBD) & 0x8000:        # VK_OEM_MINUS
                bump_opacity(-0.03)
            elif u.GetAsyncKeyState(0xBB) & 0x8000:      # VK_OEM_PLUS
                bump_opacity(+0.03)
        if not grab["dragging"]:                         # 拖到一半放開 Ctrl+Alt 不要斷手
            set_grab(holding)
        # 調完透明度節流寫檔，不要每 80ms 落一次磁碟
        if opa["dirty"] and _now() - opa["wrote_at"] > 0.6:
            c = read_config()
            c["opacity"] = round(opa["v"], 3)
            write_config(c)
            opa.update(dirty=False, wrote_at=_now())
            cfg_seen["mtime"] = _cfg_mtime()
        root.after(80, input_poll)

    def reload_config_if_changed() -> None:
        """外面下 `notice.py config --opacity ...` 時，不用重開 HUD 就吃到新設定。"""
        m = _cfg_mtime()
        if m == cfg_seen["mtime"]:
            return
        cfg_seen["mtime"] = m
        c = read_config()
        v = clamp_opacity(c.get("opacity"))
        if abs(v - opa["v"]) > 1e-6:
            apply_opacity(v)
        if c.get("border", True) != cfg.get("border", True):
            cfg["border"] = c.get("border", True)
        if c["exclude_from_capture"] != cfg.get("exclude_from_capture", True):
            cfg["exclude_from_capture"] = c["exclude_from_capture"]
            for h in (hwnd, hwnd_pill):
                set_capture_excluded(h, c["exclude_from_capture"])
        if c.get("pill_x") is None or c.get("pill_y") is None:
            pillpos["x"] = pillpos["y"] = None           # 回到預設位置
        elif (c["pill_x"], c["pill_y"]) != (pillpos["x"], pillpos["y"]):
            pillpos["x"], pillpos["y"] = c["pill_x"], c["pill_y"]
        layout["key"] = None                             # 逼 draw_panel 重排一次

    draw_border(ACCENT)
    draw_panel(rows0)
    show_windows()
    if fg_locked:
        _u.LockSetForegroundWindow(2)                  # LSFW_UNLOCK，別讓其他程式一直搶不到前景

    tick = {"n": 0}

    def loop() -> None:
        tick["n"] += 1
        n = tick["n"]
        guard()
        if n <= 6:
            cur = toplevel_hwnd(root)
            ex = ctypes.windll.user32.GetWindowLongW(cur, -20)
            trace(f"tick{n} hwnd={cur} same={cur==hwnd} ex=0x{ex & 0xffffffff:08x} "
                  f"fg={ctypes.windll.user32.GetForegroundWindow()}")

        if flags["abort"]:
            # Ctrl+Alt+Q＝整台電腦的控制權都收回來，每一條都停
            rows = active_sessions()
            write_json(ABORT, {"at": _now(), "how": T("how_hotkey"),
                               "detail": rows[0].get("detail", "") if rows else ""})
            for r in rows:
                stop_session(r["sid"])
            shutdown()
            return

        rows = active_sessions()
        if not rows:
            shutdown()
            return

        reload_config_if_changed()
        draw_panel(rows)

        # 呼吸燈：表頭的點與外框一起明暗，讓它看起來是活的
        bright = (n // 4) % 2 == 0
        pcv.itemconfig(layout["header_dot"], fill=ACCENT if bright else ACCENT_DIM)
        if rows[0].get("border", True) and cfg.get("border", True):
            cv.itemconfig("border", fill=ACCENT if bright else ACCENT_DIM)
        else:
            cv.itemconfig("border", fill=TRANS)

        now = _now()
        for r in layout["rows"]:
            # 換動作的那 0.4 秒把點打亮，一眼看得出剛剛動了一下
            lit = now - flash.get(r["sid"], 0) < 0.4
            pcv.itemconfig(r["dot_id"],
                           fill=TEXT_MAIN if lit else (r["color"] if bright else r["dim"]))
            pcv.itemconfig(r["time_id"], text=fmt_elapsed(now - r["started_at"]))

        if n % 8 == 0:
            keep_topmost(hwnd)
            keep_topmost(hwnd_pill)
        if n % 4 == 0:
            write_json(BEAT, {"ts": _now(), "pid": os.getpid()})

        root.after(250, loop)

    def shutdown() -> None:
        flags["quit"] = True
        if opa["dirty"]:                                 # 剛調到一半的透明度別掉了
            try:
                c = read_config()
                c["opacity"] = round(opa["v"], 3)
                write_config(c)
            except Exception:
                pass
        try:
            BEAT.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    trace("entering mainloop")
    write_json(BEAT, {"ts": _now(), "pid": os.getpid()})
    root.after(10, guard, 120)   # 前 3 秒每 25ms 搶回一次
    root.after(60, loop)
    root.after(120, input_poll)
    root.mainloop()
    trace("mainloop returned")


# ---------------------------------------------------------------- CLI 指令


def cmd_start(args) -> int:
    st = ensure_overlay(args.title, args.detail, args.ttl, args.pos,
                        not args.no_border)
    deadline = _now() + 6
    while _now() < deadline and not overlay_alive():
        time.sleep(0.2)
    ok = overlay_alive()
    print((T("cli_lit") if ok else T("cli_lit_fail")) +
          f"：{st['title']} / {st['detail'] or '—'} / TTL {int(args.ttl)}s")
    return 0 if ok else 1


def cmd_set(args) -> int:
    st = read_json(STATE)
    if not st or not overlay_alive():
        return cmd_start(args)
    if args.detail is not None:
        st["detail"] = args.detail
    if args.title:
        st["title"] = args.title
    if args.ttl:
        st["expires_at"] = _now() + float(args.ttl)
    st["updated_at"] = _now()
    write_json(STATE, st)
    print(T("cli_updated", detail=st.get("detail") or "-"))
    return 0


def cmd_stop(_args) -> int:
    """手動熄燈：把每一條都收掉（hook 的回合結束只收自己那一條）。"""
    st = read_json(STATE)
    for one in active_sessions():
        stop_session(one["sid"])
    stop_session(None)
    deadline = _now() + 3
    while _now() < deadline and overlay_alive():
        time.sleep(0.2)
    if overlay_alive():
        pid = read_json(BEAT).get("pid")
        if pid:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True)
    print(T("cli_off") + (T("cli_aborted") if st.get("aborted") else ""))
    return 0


def _park_pos(edge: str) -> tuple[int, int]:
    """把膠囊推到某一邊的畫面外，只留 PANEL_HANDLE 那一截。

    不想看到它、又懶得用滑鼠拖的時候用；`--reset-pos` 把它叫回來。
    寬高用估的就好：overlay 自己還會再夾一次（clamp_pill），不會真的飛出去。
    """
    wx, wy, ww, wh = work_area()
    guess_w, guess_h = 700, 70
    return {
        "left": (wx - guess_w + PANEL_HANDLE, wy + wh - guess_h - 16),
        "right": (wx + ww - PANEL_HANDLE, wy + wh - guess_h - 16),
        "bottom": (wx + (ww - guess_w) // 2, wy + wh - PANEL_HANDLE),
        "top": (wx + (ww - guess_w) // 2, wy - guess_h + PANEL_HANDLE),
    }[edge]


def cmd_config(args) -> int:
    """調外觀（透明度、膠囊位置）。HUD 正亮著的話會即時吃到，不用重開。"""
    cfg = read_config()
    if args.opacity is not None:
        cfg["opacity"] = round(clamp_opacity(args.opacity), 3)
    if args.reset_pos:
        cfg.pop("pill_x", None)
        cfg.pop("pill_y", None)
    if args.x is not None:
        cfg["pill_x"] = int(args.x)
    if args.y is not None:
        cfg["pill_y"] = int(args.y)
    if args.capture is not None:
        cfg["exclude_from_capture"] = args.capture == "hide"
    if args.hook_ttl is not None:
        cfg["hook_ttl"] = max(30.0, float(args.hook_ttl))
    if args.border is not None:
        cfg["border"] = args.border == "on"
    if args.lang is not None:
        cfg["lang"] = args.lang
        _LANG["v"] = None if args.lang == "auto" else args.lang
    if args.park:
        cfg["pill_x"], cfg["pill_y"] = _park_pos(args.park)
    write_config(cfg)
    print(json.dumps({
        "opacity": cfg.get("opacity"),
        "exclude_from_capture": cfg.get("exclude_from_capture"),
        "border": cfg.get("border"),
        "lang": cfg.get("lang", "auto"),
        "lang_in_use": ui_lang(),
        "hook_ttl": cfg.get("hook_ttl"),
        "triggers": load_triggers(),
        "pill_x": cfg.get("pill_x"),
        "pill_y": cfg.get("pill_y"),
        "applied_live": overlay_alive(),
        "file": str(CONFIG),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_status(_args) -> int:
    alive = overlay_alive()
    rows = active_sessions()
    print(json.dumps({
        "alive": alive,
        "sessions": [{
            "sid": r["sid"],
            "label": r.get("label") or "",
            "color": SESSION_COLORS[int(r.get("color", 0)) % len(SESSION_COLORS)],
            "detail": r.get("detail"),
            "elapsed": fmt_elapsed(_now() - float(r.get("started_at", _now()))),
            "expires_in": round(float(r.get("expires_at", 0)) - _now(), 1),
        } for r in rows],
        "aborted": bool(abort_latched()),
        "pid": read_json(BEAT).get("pid"),
        "opacity": read_config().get("opacity"),
        "pill_pos": [read_config().get("pill_x"), read_config().get("pill_y")],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_check_abort(_args) -> int:
    latch = abort_latched()
    if latch or read_json(STATE).get("aborted"):
        when = time.strftime("%H:%M:%S", time.localtime(latch.get("at", _now())))
        print(T("cli_abort_msg", when=when,
                how=latch.get("how") or T("how_hotkey"),
                detail=latch.get("detail") or "-"))
        return 3
    print(T("cli_ok"))
    return 0


def cmd_clear_abort(_args) -> int:
    """解除終止 latch。只有使用者下一則訊息（UserPromptSubmit hook）會呼叫它。"""
    had = bool(abort_latched())
    try:
        ABORT.unlink(missing_ok=True)
    except Exception:
        pass
    if ABORT_DIR.exists():                      # 用停止鈕單獨停掉的那幾條也一起解除
        for f in ABORT_DIR.glob("*.json"):
            had = True
            try:
                f.unlink()
            except OSError:
                pass
    print(T("cli_cleared") if had else T("cli_not_latched"))
    return 0


# ---------------------------------------------------------------- hooks

# 會借走使用者實體鍵鼠／他自己那顆 Chrome 的東西。
# Bash／PowerShell 那邊的關鍵字在 triggers.txt／triggers.local.txt（見 load_triggers）。
CHROME_TOOL = re.compile(r"^mcp__claude-in-chrome__")


def trigger_hit(cmd: str) -> str | None:
    low = cmd.lower()
    for w in load_triggers():
        if w.lower() in low:
            return w
    return None


# 純讀取、不影響使用者的就別亮燈
PASSIVE_TOOL = {
    "mcp__claude-in-chrome__list_connected_browsers",
    "mcp__claude-in-chrome__tabs_context_mcp",
}

DETAIL_BUDGET = 96          # 膠囊那一行放得下的字數


def _one_line(value, limit: int = DETAIL_BUDGET) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _url_text(url: str) -> str:
    """網址留主機 + 路徑，讓使用者看得出是哪一頁，不是只有網域。"""
    text = str(url)
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return _one_line(text, 64)


def _where(ti: dict) -> str:
    coord = ti.get("coordinate")
    if isinstance(coord, (list, tuple)) and len(coord) == 2:
        return T("where_coord", x=coord[0], y=coord[1])
    if ti.get("ref"):
        return T("where_ref", ref=ti["ref"])
    return T("where_page")


def _kv_dump(ti: dict, limit: int = DETAIL_BUDGET) -> str:
    """通用回退：把參數攤平寫出來。

    有它才不會因為出現還沒對照過的新工具，畫面上就只剩一個工具名。
    """
    parts = []
    for k, v in ti.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        parts.append(f"{k}={_one_line(v, 40)}")
    sep = "、" if ui_lang() == "zh" else ", "
    return _one_line(sep.join(parts), limit) if parts else T("no_args")


def describe_chrome(short: str, ti: dict) -> str:
    if short == "computer":
        act = str(ti.get("action", "?"))
        act_name = T("act_" + act) if ("act_" + act) in STRINGS["en"] else act
        if act == "type":
            return T("c_type", text=_one_line(ti.get("text", ""), 60))
        if act == "key":
            rep = ti.get("repeat")
            keys = ti.get("text", "")
            return T("c_key_rep", keys=keys, n=rep) if rep else T("c_key", keys=keys)
        if act in ("left_click", "right_click", "double_click", "triple_click", "hover"):
            mod = ti.get("modifiers")
            if mod:
                return T("c_click_mod", act=act_name, where=_where(ti), mod=mod)
            return T("c_click", act=act_name, where=_where(ti))
        if act == "scroll":
            return T("c_scroll", dir=ti.get("scroll_direction", "?"),
                     amount=ti.get("scroll_amount", 3), where=_where(ti))
        if act == "left_click_drag":
            return T("c_drag", a=ti.get("start_coordinate"), b=ti.get("coordinate"))
        if act == "wait":
            return T("c_wait", sec=ti.get("duration"))
        if act == "scroll_to":
            return T("c_scroll_to", where=_where(ti))
        return T("c_other", act=act_name)
    if short == "navigate":
        return T("navigate", url=_url_text(ti.get("url", "")))
    if short == "form_input":
        return T("form_input", ref=ti.get("ref", ""),
                 value=_one_line(ti.get("value", ""), 50))
    if short == "file_upload":
        return T("file_upload",
                 path=_one_line(ti.get("filePath") or ti.get("file_path") or "", 60))
    if short == "upload_image":
        return T("upload_image")
    if short == "javascript_tool":
        return T("javascript", code=_one_line(ti.get("text", ""), 60))
    if short == "find":
        return T("find", query=_one_line(ti.get("query", ""), 40))
    if short in ("read_page", "get_page_text"):
        return T("read_page")
    if short == "read_console_messages":
        return T("read_console")
    if short == "read_network_requests":
        return T("read_network")
    if short == "tabs_create_mcp":
        return T("tab_new")
    if short == "tabs_close_mcp":
        return T("tab_close", tab=ti.get("tabId", "")).strip()
    if short == "shortcuts_execute":
        return T("shortcut",
                 name=_one_line(ti.get("name") or ti.get("shortcut") or "", 40))
    if short == "resize_window":
        return T("resize", size=ti.get("preset") or f"{ti.get('width')}x{ti.get('height')}")
    if short == "gif_creator":
        return T("gif")
    if short in ("select_browser", "switch_browser"):
        return T("switch_browser",
                 id=ti.get("deviceId") or ti.get("browserId") or "").strip()
    if short == "browser_batch":
        actions = ti.get("actions")
        if isinstance(actions, list) and actions:
            steps = []
            for a in actions:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name", "?"))
                inner = a.get("input") if isinstance(a.get("input"), dict) else {}
                if name == "computer":
                    name = str(inner.get("action", "computer"))
                elif name == "navigate" and inner.get("url"):
                    name = "navigate " + _url_text(inner["url"])[:24]
                steps.append(name)
            return T("batch", n=len(actions),
                     steps=_one_line(" → ".join(steps), 70))
        return T("batch_plain")
    return T("unknown", tool=short, args=_kv_dump(ti))


def describe_shell(cmd: str, hit: str | None = None) -> str:
    script = re.search(r"[\w\-./\\:]+\.(?:ps1|py|bat|cmd|exe)", cmd)
    what = script.group(0).replace("\\", "/").rsplit("/", 1)[-1] if script else (
        hit or T("shell_generic"))
    return T("shell", what=what, cmd=_one_line(cmd, 70))


def _reason(tool: str, tool_input: dict) -> str | None:
    if CHROME_TOOL.match(tool):
        if tool in PASSIVE_TOOL:
            return None
        return _one_line(describe_chrome(tool.rsplit("__", 1)[-1], tool_input))
    if tool in ("Bash", "PowerShell"):
        cmd = str(tool_input.get("command", ""))
        hit = trigger_hit(cmd)
        if hit:
            return _one_line(describe_shell(cmd, hit))
    return None


def _payload_detail(raw: str) -> tuple[str, str | None]:
    payload = json.loads(raw or "{}")
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    return tool, _reason(tool, ti)


def _payload_meta(payload: dict) -> tuple[str, str]:
    """(session_id, 專案名)。專案名取 cwd 的最後一段：同時有好幾條在跑時，
    「automation-hud 正在點 Chrome」比一串 uuid 有用得多。"""
    sid = str(payload.get("session_id") or "") or "manual"
    cwd = str(payload.get("cwd") or "")
    label = Path(cwd).name if cwd else ""
    return sid, label


def cmd_hook_pre(_args) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
        sid, label = _payload_meta(payload)
        _tool, reason = _payload_detail(raw)
        if not reason:
            return 0
        latch = abort_latched(sid)
        if latch:
            when = time.strftime("%H:%M:%S", time.localtime(latch.get("at", _now())))
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": T(
                    "deny", when=when,
                    how=latch.get("how") or T("how_hotkey"),
                    detail=latch.get("detail") or "-"),
            }}, ensure_ascii=False))
            return 0
        ensure_overlay(T("title"), reason, ttl=read_config()["hook_ttl"],
                       pos="bottom", border=True, sid=sid, label=label)
    except Exception:
        pass
    return 0  # 永遠 0：hook 不能擋掉工具呼叫


def cmd_describe(_args) -> int:
    """把一份 PreToolUse payload 餵進來，印出提示層上會出現的那行字。"""
    tool, reason = _payload_detail(sys.stdin.read())
    print(f"{tool}")
    print(f"  -> {reason if reason else T('cli_no_light')}")
    return 0


def _hook_sid() -> str:
    try:
        return _payload_meta(json.loads(sys.stdin.read() or "{}"))[0]
    except Exception:
        return "manual"


def cmd_hook_prompt(_args) -> int:
    """UserPromptSubmit：使用者開口了，代表控制權在他手上。

    解除終止 latch，並且一定要把提示層熄掉：他中斷回合時 Claude Code 不會送出
    Stop 事件，HUD 只能等 TTL 自己過期，在他打下一則訊息的這段時間會一直亮著計時
    （2026-09-12 實測卡到 07:56）。他開口＝控制權已經回到他手上，沒有理由還亮著。
    """
    sid = _hook_sid()
    try:
        if abort_latched(sid):
            _quietly(cmd_clear_abort, _args)
    except Exception:
        pass
    _end_turn(sid)
    return 0


def cmd_hook_stop(_args) -> int:
    _end_turn(_hook_sid())
    return 0


def _quietly(fn, *a) -> None:
    """hook 的 stdout 會被 Claude Code 塞進對話（UserPromptSubmit 尤其如此），
    提示層的收尾訊息不該佔用模型的 context，所以 hook 路徑一律不印。"""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        fn(*a)


def _end_turn(sid: str) -> None:
    """回合結束：把自己這一條收掉（別條還在跑就繼續亮著）。不管 overlay 活不活著都寫
    stop=True，下一回合才會重新計時（overlay 可能早就因為 TTL 自己熄了）。
    順便做不急的維護：清孤兒暫存檔、更新觸發清單。"""
    try:
        stop_session(sid)
    except Exception:
        pass
    try:
        sweep_stale_tmp()
        compile_triggers()
    except Exception:
        pass


# ---------------------------------------------------------------- main


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Claude 自動化提示層")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--title", default=None, help="主標題")
        sp.add_argument("--detail", default=None, help="目前在做什麼")
        sp.add_argument("--ttl", type=float, default=900, help="幾秒後自動消失")
        sp.add_argument("--pos", choices=["top", "bottom"], default=None)
        sp.add_argument("--no-border", action="store_true", help="不畫螢幕四邊框")

    add_common(sub.add_parser("start"))
    add_common(sub.add_parser("set"))
    sub.add_parser("stop")
    sub.add_parser("status")
    sp_cfg = sub.add_parser("config", help="調透明度／膠囊位置（即時生效，會記住）")
    sp_cfg.add_argument("--opacity", type=float, default=None,
                        help=f"{MIN_OPACITY}~{MAX_OPACITY}，越小越透明（預設 {DEFAULT_OPACITY}）")
    sp_cfg.add_argument("--x", type=int, default=None, help="膠囊左上角 X")
    sp_cfg.add_argument("--y", type=int, default=None, help="膠囊左上角 Y")
    sp_cfg.add_argument("--reset-pos", action="store_true", help="位置恢復預設（底部置中）")
    sp_cfg.add_argument("--capture", choices=["hide", "show"], default=None,
                        help="hide＝截圖／錄影／直播拍不到提示層（預設）；show＝拍得到")
    sp_cfg.add_argument("--hook-ttl", type=float, default=None,
                        help=f"兩個自動化動作之間最多隔幾秒還亮著（預設 {DEFAULT_HOOK_TTL}，最少 30）")
    sp_cfg.add_argument("--border", choices=["on", "off"], default=None,
                        help="螢幕四邊的橘框（off＝不想看到就關掉，膠囊還在）")
    sp_cfg.add_argument("--lang", choices=["auto", "zh", "en"], default=None,
                        help="介面語言 / overlay language (auto = follow Windows)")
    sp_cfg.add_argument("--park", choices=["left", "right", "top", "bottom"], default=None,
                        help="把膠囊推到那一邊的畫面外，只留一小截；--reset-pos 叫回來")
    sub.add_parser("compile-triggers", help="重新產生給 hook 用的關鍵字清單")
    sub.add_parser("check-abort")
    sub.add_parser("clear-abort")
    sub.add_parser("hook-pre")
    sub.add_parser("hook-prompt")
    sub.add_parser("describe")
    sub.add_parser("hook-stop")
    sub.add_parser("_overlay")

    trace(f"main argv={sys.argv[1:]}")
    args = p.parse_args()
    trace(f"parsed cmd={args.cmd}")
    if args.cmd == "_overlay":
        # pythonw 沒有 console，出事會靜默消失 → 一定要留 traceback
        try:
            run_overlay()
        except Exception:
            import traceback
            APP_DIR.mkdir(parents=True, exist_ok=True)
            (APP_DIR / "error.log").write_text(
                time.strftime("%Y-%m-%d %H:%M:%S\n") + traceback.format_exc(),
                encoding="utf-8")
            raise
        return 0
    return {
        "start": cmd_start,
        "set": cmd_set,
        "stop": cmd_stop,
        "status": cmd_status,
        "config": cmd_config,
        "compile-triggers": lambda _a: (compile_triggers(), print(T("cli_triggers")))[1] or 0,
        "check-abort": cmd_check_abort,
        "clear-abort": cmd_clear_abort,
        "hook-pre": cmd_hook_pre,
        "hook-prompt": cmd_hook_prompt,
        "describe": cmd_describe,
        "hook-stop": cmd_hook_stop,
    }[args.cmd](args)


if __name__ == "__main__":
    trace("module loaded")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
