# automation-notice — 自動化提醒視窗

[English](README.md) | **繁體中文**

Claude Code 在操控你已登入的 Chrome（Claude in Chrome）、或跑前景鍵鼠腳本時，畫面上會直接亮出提示，
不再只靠對話裡打一行字拜託你別動鍵盤。

畫面長這樣（螢幕四邊橘框 + 一塊膠囊面板）：

```
● Claude 自動化中                                    Ctrl+Alt+Q 收回控制權
● NewGame            在 Chrome 左鍵點擊 座標 (640, 360)         01:24
● automation-notice  開啟網址 example.com/jobs/1234             00:18
```

每一行是一個 Claude Code session，有自己的顏色、專案名（取那個 session 的工作目錄）、
與計時。只開一個 Claude 時就只有一行。

動作那一欄寫的是**這一步實際會動到什麼**，不是工具名稱：點了哪個座標或哪個元素、
輸入了哪一串字、開了哪個完整網址、表單填了什麼值、批次操作有幾步分別是什麼。
遇到還沒對照過的新工具，會直接把它的參數攤平寫出來（`工具名：key=value、key=value`），
所以不會因為工具更新就變成一行看不懂的東西。動作換了那一行的圓點會亮一下，好認出它剛動過。

文字由本機的 hook 算出來，不經過模型，所以不花 token；hook 也不會往對話裡印任何東西。

## 你只需要知道四件事

- **它不會擋到你**：整層點擊穿透、不搶焦點、不進工作列與 Alt+Tab，滑鼠點下去是點到底下的視窗。
- **停手是按快捷鍵，不是按鈕**：`Ctrl+Alt+Q` 收回控制權。畫面上刻意沒有任何可以點的東西——
  會吃滑鼠的視窗，剛好擋到自動化那一下點擊就會把它吞掉。停手會留下**會黏著的終止 latch**：
  在你自己開口之前，PreToolUse hook 會**直接擋掉**所有會借走你鍵鼠的工具呼叫（回 `deny`
  並附上你是幾點幾分中斷、當時 AI 正在做什麼），AI 只能停手回報，不會自己再試第二次。
  你下一則訊息一送出就自動解除。
- **不想看到就推走**：膠囊可以拖出畫面外，只留一小截當把手（`config --park right` 一行也行），
  `config --border off` 關掉螢幕四邊的橘框，`config --reset-pos` 把它叫回來。
- **截圖、錄影、直播都拍不到它**（預設）：只有你的眼睛看得到。AI 自己截圖時不會被它擋住，
  直播或錄影也不會把 AI 正在輸入的內容拍出去。想讓錄影拍得到就 `config --capture show`。

膠囊全程點擊穿透，只有你**按住 Ctrl+Alt** 時才暫時吃得到滑鼠，好讓你把它拖到別的地方；
放開就恢復穿透。上面沒有任何可以點的東西，這是刻意的。

## 安裝

需求：Windows 10 2004 以上、帶 tkinter 的 Python 3.9+（python.org 的安裝檔預設就有）、Claude Code。

當成 Claude Code plugin 裝（別人用這個）：

```
/plugin marketplace add <owner>/<repo>
/plugin install automation-notice
```

或直接接到自己的 `~/.claude/settings.json`：

```bash
python install.py              # 安裝或更新；動 settings.json 之前會自動備份到 ~/.claude/backups/
python install.py --dry-run    # 只看會寫進去什麼
python install.py --uninstall  # 拔掉（其他 hook 不動）
```

`install.py` 會用「執行它的那個 Python」，不用手改路徑；資料夾搬家後重跑一次就好。
plugin 版本用 PATH 上的 `python`。

## 什麼時候會亮

| 事件 | 條件 | 動作 |
| --- | --- | --- |
| PreToolUse | `mcp__claude-in-chrome__*`（純查詢的 `tabs_context` / `list_connected_browsers` 除外） | 那條 session 亮起或續期 |
| PreToolUse | `Bash` / `PowerShell` 指令文字裡有觸發關鍵字 | 同上 |
| Stop / SessionEnd | 該 session 回合結束 | 收掉那一行（別條繼續亮） |
| UserPromptSubmit | 你送出新訊息 | 收掉那一行並解除終止 latch |

內建瀏覽器（`mcp__Claude_Browser__*`）不觸發：它在側邊面板裡跑，不會借走你的鍵鼠。

**觸發關鍵字**寫在兩個檔：`triggers.txt` 是通用預設（`SendKeys`、`SetForegroundWindow`、`pyautogui` 等），
`triggers.local.txt` 放你自己的前景腳本名稱（不進 repo）。每行一個字面關鍵字、不分大小寫，`#` 開頭是註解。
改完不用重裝，下一個回合結束時自動生效。Bash／PowerShell 每個指令都會觸發 hook，所以先在 shell 裡用
`grep -F` 過濾，沒命中就不會啟動 Python，一般指令不會被拖慢。

**會亮多久**：每個自動化動作都會把期限往後延 `hook_ttl` 秒（預設 180）。所以 AI 做多久都會一直亮著，
只有「兩個動作之間」隔超過 180 秒才會先熄，下一個動作再亮起來時計時會接著算，不會歸零；
回合結束才重新計時。這個期限是保險：AI 當掉或回合被中斷時，提示層最多 180 秒後自己消失。

## 設定

```bash
python notice.py config --opacity 0.5              # 透明度 0.2～1.0（預設 0.72）
python notice.py config --capture show             # 截圖／錄影拍得到（預設 hide＝拍不到）
python notice.py config --border off               # 關掉螢幕四邊的橘框
python notice.py config --park right               # 推出畫面外，只留一小截（left/right/top/bottom）
python notice.py config --reset-pos                # 位置回到底部置中
python notice.py config --hook-ttl 300             # 兩個動作之間最多隔幾秒還亮著（預設 180，最少 30）
python notice.py config --lang en                  # 介面語言（預設跟著 Windows 顯示語言走）
```

畫面上：按住 Ctrl+Alt 膠囊就能抓著拖走（放開恢復點擊穿透），Ctrl+Alt+- / = 或按住時滾輪調透明度。
設定都會記住，HUD 亮著的時候改也會即時生效。

## 手動用法

```bash
python notice.py start --detail "批次填寫表單" --ttl 600
python notice.py set   --detail "第 9/15 筆"      # 不重啟就換文字
python notice.py status                           # 目前有哪幾條在跑、各自什麼顏色
python notice.py check-abort                      # 被停過就 exit 3
python notice.py clear-abort                      # 解除終止 latch（平常 hook 會自動做）
python notice.py stop                             # 全部熄掉
python selftest.py                             # 不開 GUI 的自測（描述／版面／latch／分色）

# 想知道某個工具呼叫在畫面上會顯示成什麼，直接餵 payload 進去看
echo '{"tool_name":"mcp__claude-in-chrome__computer","tool_input":{"action":"type","text":"你好"}}' | python notice.py describe
```

## 限制

- 只支援 Windows。
- 介面文字有中英兩種（跟著 Windows 顯示語言，`config --lang` 可指定）；程式碼註解只有中文。
- **不會在被點的控制項上標記位置**。Claude in Chrome 給的是瀏覽器內座標，換算成螢幕座標要猜
  Chrome 視窗與視埠的位移，猜錯就是標在無關的地方，寧可不做——座標直接寫在文字裡。
- Bash／PowerShell 是看指令**文字**裡有沒有關鍵字，所以會誤判：指令只是提到 `SetForegroundWindow`
  這個字（例如在寫文件）也會亮。誤判只會多亮一下，不影響任何操作。
- 它只負責讓你看得到，不判斷動作危不危險；權限還是要在 Claude Code 自己的權限設定裡把關。
- 官方 computer use 的操作不經過 PreToolUse hook 的話就不會亮（尚未驗證）。

## 執行期檔案

放 `%LOCALAPPDATA%\ClaudeAutomationNotice\`：

| 檔案 | 誰寫 | 用途 |
| --- | --- | --- |
| `sessions/<session_id>.json` | CLI／hook | 每條 session 現在在做什麼（手動啟動的是 `manual.json`） |
| `aborts/<session_id>.json` | overlay | 單條 session 的黏著 latch（CLI 用） |
| `abort.json` | overlay | Ctrl+Alt+Q 的全域 latch |
| `heartbeat.json` | overlay | pid + 時間戳，用來判斷還活著沒 |
| `config.json` | CLI | 你的設定 |
| `triggers.grep` | hook | 兩份關鍵字檔整理後給 grep 用 |

在那個資料夾建一個空檔 `DEBUG`，overlay 就會把開機階段寫進 `boot.log`（pythonw 沒有 console，只能這樣看）。

## 踩過的坑（改之前先看）

- **不能用 `deiconify()` 顯示視窗**：它走 `ShowWindow(SW_NORMAL)` 會 activate，就算掛了
  `WS_EX_NOACTIVATE` 一樣把焦點搶走。要用 `ShowWindow(hwnd, SW_SHOWNOACTIVATE)`。
- **Tk 建第一個視窗時會主動 `SetActiveWindow`**（tkWinWm.c UpdateWrapper 的 firstWindow 分支），
  而剛啟動的進程有搶前景的權利 → 視窗還沒顯示，第一次 `update_idletasks` 就把前景拿走。
  只有使用者一陣子沒按鍵（看影片）時才重現，打遊戲時不會，所以只測一次會以為沒事。
  解法是 overlay 一開機、建 Tk 之前先 `LockSetForegroundWindow(LSFW_LOCK)`，顯示完再 `LSFW_UNLOCK`。
  拿掉 `-topmost` 沒用。找法是在每一步之後記一次 `GetForegroundWindow()`。
- **Tk 進 `mainloop()` 後還是可能把視窗拉到前景**，所以另外掛了前景守衛
  （前 3 秒每 25ms、之後每 250ms 檢查一次，發現前景是自己就還回去）。它是保險，不是主要防線。
- **還焦點的目標要驗過**：`SetForegroundWindow` 對 cloaked／隱形視窗會回報成功，但前景其實還停在 HUD 上。
  目標優先用 `hook-pre` 在啟動 overlay 前記下的前景（session 檔的 `restore_fg`）；備援挑視窗時要跳過
  置頂視窗，不然會挑到「快顯主機」這種系統彈窗。原本的前景若已經被關掉，`next_activatable_window()` 會重挑。
- **先排版再顯示**：`draw_panel()` 之後才 `ShowWindow(SW_SHOWNOACTIVATE)`，還原迴圈只在前景是 HUD 自己時才出手，
  使用者自己切視窗時不能把他拉回去。
- **Tk 的滑鼠事件看真實游標位置**，不是訊息裡帶的座標。所以 `PostMessage` 塞一組假座標測不了按鈕，
  自動化測試只能把視窗移到游標底下再送訊息（或者接受這一段要人工點一次）。
- **終止 latch 必須放在狀態檔以外的地方**：`ensure_overlay()` 每次亮燈都會重寫該 session 的狀態，
  旗標放在裡面會被下一個動作洗掉，結果就是使用者主動終止後又亮第二次。
- **使用者在介面按停止中斷回合時，Claude Code 不會送 Stop 事件**，所以熄燈也掛在 UserPromptSubmit。
- **一個 session 一個狀態檔**：以前多個 hook 進程搶寫同一個 `state.json`，Windows 上 `os.replace` 碰到
  目標檔正被讀會失敗，累積出一堆孤兒暫存檔。拆成一條一個檔之後幾乎不會撞；仍保留重試與
  `sweep_stale_tmp()`（要用 `rglob`，子資料夾裡的也要清）。
- **常駐進程用 `CREATE_BREAKAWAY_FROM_JOB`**，不要用 WMI。agent 的 shell 在 KILL_ON_JOB_CLOSE 的
  job 裡，一般子進程會跟著那次工具呼叫被殺；WMI 建出來的進程在某些機器上會靜默死掉，連 notice.py 第一行
  都沒跑到（實測過，boot.log 全空）。
