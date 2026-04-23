# Claude Buddy Serial — Linux 远端 / Windows 本地工作流

> 本文档适用的场景:**Claude Code 跑在远端 Linux 服务器**(SSH / XPipe / VS Code Remote 登上去用),
> **M5StickC Plus 硬件插在本地 Windows**。
>
> 原项目 [`README.md`](./README.md) 描述的是 Claude Desktop + 硬件都在同一台 Windows 的用法,
> 不适用本文档场景,但二者互不冲突,想回退可以随时切。
>
> 架构灵感和端口习惯和 [`/volume/pt-dev/users/yuyang/config/claw-jump`](../config/claw-jump) 对齐
> (两者都是"Linux hook → SSH RemoteForward → Windows agent"),可并行跑。

---

## 目录

- [为什么需要这套](#为什么需要这套)
- [架构](#架构)
- [第一部分:硬件 & 固件](#第一部分硬件--固件)
- [第二部分:Windows 侧 agent](#第二部分windows-侧-agent)
- [第三部分:SSH RemoteForward](#第三部分ssh-remoteforward)
- [第四部分:Linux 侧 bridge + Claude Code hook](#第四部分linux-侧-bridge--claude-code-hook)
- [全链路验证](#全链路验证)
- [常见问题](#常见问题)
- [和 claw-jump 共存](#和-claw-jump-共存)
- [回退到原 Windows 单机版](#回退到原-windows-单机版)

---

## 为什么需要这套

原项目 `tools/serial_bridge.py` 的假设是:**Claude Desktop + M5StickC Plus 都在同一台 Windows**。
一个 Python 进程既扫本地 `~/.claude/projects/**/*.jsonl` 又开 COM 串口。

实际工作流是 **Claude Code 在远端 Linux、USB 设备在本地 Windows**,一个进程做不到:
jsonl 在远端,串口必须本地。所以把原脚本拆成两半,中间用 HTTP + SSH RemoteForward 相连。

---

## 架构

```
───────────────── Linux 远端 ─────────────────              ────── Windows 本地 ──────

claude code
  │ PreToolUse hook                                         buddy_serial_agent.py
  ↓                                                         ├─ POST /state  → 串口
tools/hook_permission.py  (原版,不改)                       ├─ POST /prompt → 串口 + 阻塞等 A/B
  │ POST http://127.0.0.1:19191/permission                  ├─ 串口读 cmd=permission → 回 /prompt
  ↓                                                         └─ GET  /health
tools/serial_bridge_linux.py  (新)                          │
  ├─ 扫 Linux 本地 ~/.claude/projects/**/*.jsonl            │
  ├─ 每 0.8s 算一份 state payload                           │
  ├─ POST /state  ────────┐                                 │
  └─ POST /prompt ────────┼── SSH RF 47654 ────────────→  127.0.0.1:47654
                          │                                 │
                          │                                 ↓  pyserial COM4
     hook_permission.py ──→ /permission :19191              │
                                                            M5StickC Plus
```

| 链路 | Linux HTTP | Windows HTTP | Windows 进程 | 触发 hook |
|---|---|---|---|---|
| claw-jump toast | —(hook 直 POST) | `:47653` | `claw_agent.py` | `Stop` / `Notification` |
| **buddy serial 硬件** | `:19191` | `:47654` | `buddy_serial_agent.py` | `PreToolUse` |

---

## 第一部分:硬件 & 固件

一次性操作,烧完就不用再碰 C++ 工具链。

### 1.1 硬件准备

**买对板子**:`platformio.ini` 锁定的是 **M5StickC Plus**(1.14" 彩屏那款)。

| 型号 | 能不能用 | 备注 |
|---|---|---|
| M5StickC Plus | ✅ 直接用 | 默认目标,`board = m5stick-c` |
| M5StickC(老款) | ❌ | 屏幕不同,不支持 |
| M5StickC Plus 2(2024 款) | ⚠️ 需改板子 | `platformio.ini` 改 `board = m5stick-c-plus2`,pin 会差 |

**配一根 USB-C 数据线**(必须是数据线,纯充电线认不到串口)。

### 1.2 装驱动

M5StickC Plus 用 **Silicon Labs CP210x** 芯片做 USB↔UART。Windows 11 大多自带,缺了烧录时设备管理器看不到 COM 口。

- 下载:https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers
- 装 **CP210x Windows Drivers** → 重启
- 插上板子,设备管理器应出现:`Silicon Labs CP210x USB to UART Bridge (COM4)` 之类

### 1.3 装 PlatformIO

二选一:

**方式 A:VS Code 扩展(推荐)**
1. 装 VS Code
2. 扩展商店搜 `PlatformIO IDE`(作者 platformio)并安装
3. 重启 VS Code,左栏出现外星人图标即装好

**方式 B:纯 CLI**
```powershell
pip install platformio
pio --version
```

### 1.4 拿代码到 Windows

```powershell
# 从远端拷整个仓库到 Windows 本地
scp -r changliu:/volume/pt-dev/users/yuyang/claude-buddy-serial-port C:\Users\<你>\claude-buddy-serial-port
cd C:\Users\<你>\claude-buddy-serial-port
```

或者 git clone 你自己的 fork。

### 1.5 烧固件

> ⚠️ **烧录前先停掉 `buddy_serial_agent.py`**(它占着 COM 口,不停 `pio upload` 会报 `could not open port`)。
> 任务管理器里结束掉 `pythonw.exe` / `python.exe` 即可。

```powershell
cd C:\Users\<你>\claude-buddy-serial-port

# (首次)仅编译,顺便让 PlatformIO 下载 ESP32 工具链 ~200MB
pio run

# 烧主固件到 flash
pio run -t upload

# 烧 LittleFS 分区(存角色资源)
pio run -t uploadfs
```

**关键输出识别**:
- `Writing at 0x0000xxxx... (xx %)` — 正在烧
- `Hash of data verified.` — 分区校验通过
- `Hard resetting via RTS pin...` — 烧完,板子已复位

烧完屏幕应亮起、出现灰底金爪 logo → 默认 pet 画面。

**实时看 log(调试用)**:
```powershell
pio device monitor -b 115200    # Ctrl+C 退出
```

### 1.6 烧角色包(可选)

默认 18 个像素风宠物**已经编译进固件**(见 `src/buddies/*.cpp`),开箱即用,不用烧角色包。

如果想用 `characters/bufo/` 的 GIF 动画版本(会刷进 LittleFS):

```powershell
python tools\flash_character.py characters\bufo
```

完成后在板子上:**长按 A 键 → 菜单 → Species → 滚到 `GIF` 选 Bufo**。

### 1.7 硬件自测(不依赖任何桥)

```powershell
pio device monitor -b 115200
```

另开一个 PowerShell:
```powershell
# 手动给板子推一条状态(替换 COM4 为你的端口)
python -c "import serial,time; s=serial.Serial('COM4',115200); s.write(b'{\"total\":1,\"running\":1,\"waiting\":0,\"msg\":\"hello\",\"entries\":[\"smoke test\"]}\n'); time.sleep(1); s.close()"
```

板子屏幕上应该刷新出 `hello` / `smoke test`。这就证明固件 + 串口路径 OK,接下来的所有问题都是软件链路的。

---

## 第二部分:Windows 侧 agent

跑 `windows-agent/buddy_serial_agent.py`,职责:
- 持续打开 COM 串口(`auto` 或指定 `COM4`)
- HTTP `:47654` 监听,暴露 `/state`、`/prompt`、`/health` 给 Linux bridge 调

### 2.1 装依赖

```powershell
cd C:\Users\<你>\claude-buddy-serial-port\windows-agent
pip install -r requirements.txt
```

只有一个依赖 `pyserial`,和 claw-jump 共用同一套 Python 也可以。

### 2.2 手动起一次

```powershell
python buddy_serial_agent.py
```

控制台应该打印:
```
[serial] connected: COM4 @ 115200
[http] listening on http://127.0.0.1:47654
[buddy-serial-agent] ready. COM=auto baud=115200
```

### 2.3 无黑窗启动 + 开机自启

测通后双击 `start_buddy_serial.bat`,用 `pythonw.exe` 后台跑,无控制台窗口。

开机自启:把 `start_buddy_serial.bat` 的快捷方式扔到
```
C:\Users\<你>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\
```

和 claw-jump 的 `start_agent.bat` 并列放,两个 agent 独立跑互不干扰。

---

## 第三部分:SSH RemoteForward

把 Linux 上的 `127.0.0.1:47654` 反向映射到 Windows 上的 `127.0.0.1:47654`。
和 claw-jump 的 47653 **同一段配置**,并排追加一行即可。

### 3.1 OpenSSH(`~/.ssh/config` on Windows)

```
Host changliu
    HostName <ip>
    User yuyang
    RemoteForward 47653 127.0.0.1:47653   # claw-jump toast
    RemoteForward 47654 127.0.0.1:47654   # buddy-serial 硬件

Host beijing
    HostName <ip>
    User yuyang
    RemoteForward 47653 127.0.0.1:47653
    RemoteForward 47654 127.0.0.1:47654

# 建议全局
Host *
    ServerAliveInterval 60
    ServerAliveCountMax 20
```

### 3.2 XPipe

在对应 SSH connection 的 "SSH options" / "Extra arguments" 里加:
```
RemoteForward 47654 127.0.0.1:47654
```
或者让 XPipe 继承 `~/.ssh/config`(最省心)。

### 3.3 验证隧道

SSH 进 Linux 之后在 Linux shell 里:
```bash
curl -v http://127.0.0.1:47654/health
```

期望:`{"ok": true, "serial_connected": true, "pending": 0}`。

- `Connection refused` → Windows agent 没起 / SSH 没加 `RemoteForward`
- `serial_connected: false` → agent 起来了但 USB 没插

---

## 第四部分:Linux 侧 bridge + Claude Code hook

### 4.1 跑 bridge

```bash
# 激活 conda env(按本集群约定)
eval "$CLAUDE_PY_ACTIVATE"

# 前台跑(调试)
python /volume/pt-dev/users/yuyang/claude-buddy-serial-port/tools/serial_bridge_linux.py

# 后台跑
nohup python /volume/pt-dev/users/yuyang/claude-buddy-serial-port/tools/serial_bridge_linux.py \
    > ~/buddy-bridge.log 2>&1 &
```

默认参数已经对齐:
- `--http-port 19191`(给 `hook_permission.py` 调)
- `--agent-port 47654`(转发到 Windows agent)

端口都改过就传参覆盖:
```bash
python tools/serial_bridge_linux.py --http-port 19191 --agent-port 48000
```

### 4.2 挂 Claude Code hook

编辑 Linux 上的 `~/.claude/settings.json`(不是 Windows 那个!)。如果已经有 claw-jump 的
`Stop` / `Notification`,**只追加 `PreToolUse` 这一节**:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "/volume/pt-dev/users/yuyang/config/claw-jump/linux/hook.sh stop"
        }]
      }
    ],
    "Notification": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "/volume/pt-dev/users/yuyang/config/claw-jump/linux/hook.sh notification"
        }]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "python3 /volume/pt-dev/users/yuyang/claude-buddy-serial-port/tools/hook_permission.py"
        }]
      }
    ]
  }
}
```

`hook_permission.py`(原版零改动)的行为:
- Bash 里 `ls / cat / git status` 等**白名单只读命令**自动 allow,不打扰硬件
- 其它 tool call → POST 到 `127.0.0.1:19191/permission` → bridge → Windows agent → 硬件按键
- bridge 不可达 / agent 不可达 / 硬件超时 → hook 静默 exit 0 → 回落到 Claude 原生审批(**不会卡 Claude**)

> ⚠️ 改完必须 **重启 Claude Code 会话**(hook config 是启动时读的)。

### 4.3 settings 片段单独文件

完整片段也放在仓库里,方便直接 cat / 复制:
```
docs/linux-settings.snippet.json
```

---

## 全链路验证

按顺序检查,任一步失败就知道卡在哪一层:

1. **固件自测**:`pio device monitor` + 本地 Python 写串口(见 1.7),屏幕能刷新 → 固件 OK
2. **Windows agent**:控制台 `[serial] connected: COM4` + `[http] listening on 127.0.0.1:47654`
3. **SSH 隧道**:Linux 上 `curl http://127.0.0.1:47654/health` → `{"ok":true,"serial_connected":true}`
4. **Linux bridge**:Linux 上 `curl http://127.0.0.1:19191/health` → `{"ok":true,"mode":...}`
5. **模拟 hook 请求**(不惊动 Claude):
   ```bash
   curl -sS -X POST http://127.0.0.1:19191/permission \
     -H "Content-Type: application/json" \
     -d '{"id":"manual-01","tool":"Bash","hint":"rm -rf /"}'
   ```
   - 设备屏幕应该弹出 prompt
   - 按 **A** → `{"ok":true,"decision":"once"}`
   - 按 **B** → `{"decision":"deny"}`
   - 不按,60 秒后超时自动 deny

6. **真链路**:让 Claude 跑一个非白名单命令(例如 `pip install xxx` / `sudo apt ...`),
   设备应该弹 prompt 等你按键。

---

## 常见问题

### 固件 / 烧录类

| 现象 | 原因 | 解决 |
|---|---|---|
| `pio upload` 报 `could not open port 'COM4'` | 串口被占用(monitor / agent) | 关 monitor、杀 `pythonw.exe`,重插 USB |
| 烧完屏幕一直黑 / 只有蓝绿点 | 电池没电 + USB 供电不够 / 数据线不通数据 | 换线;长按 M5 按钮 6 秒硬重启 |
| 设备管理器没 COM 口 | CP210x 驱动没装 | 装 SiLabs 官方驱动重启 |
| `pio upload` 卡 `Connecting...` 很久后失败 | 板子没进 boot 模式 | 按住侧面电源键 → 同时触发 upload;新 ESP32 一般自动 |
| 第一次 `pio run` 卡很久没动 | 在下载 espressif32 工具链 ~200MB | 挂梯子或等,后续缓存 |
| 编译通过但屏幕花屏 | 手上是 M5StickC Plus **2** | `platformio.ini` 改 `board = m5stick-c-plus2` |

### 桥接链路类

| 现象 | 原因 | 解决 |
|---|---|---|
| Linux `curl :47654/health` 提示 `Connection refused` | SSH 隧道没建 | 检查 `~/.ssh/config` 的 `RemoteForward`,重连 SSH |
| 隧道通但 `serial_connected: false` | USB 没插 / agent 还在等 COM 口 | 看 Windows agent 控制台有没有 `[serial] connected` |
| `/permission` 返回 `agent_unreachable` | SSH 隧道断(网络抖动) | 确认 `ServerAliveInterval 60` 在;claw-jump 同时失效可印证 |
| `/permission` 返回 `serial_unavailable` | agent 在,但 COM 口断了 | 重新插 USB / 重启 agent |
| `mode` 一直 `sleep` 但 Claude 在跑 | jsonl 路径不在默认位置 | 查 `ls ~/.claude/projects/`;HOME 不一样时 wrap 一下 |
| 短时间两次相同 prompt | Claude hook 重试 | 正常,broker 按 id 区分 |
| bridge 退出时 hook 卡住 | bridge 挂了,hook 还在 POST | `hook_permission.py` 默认 65s 超时自动跳过,走原生审批 |
| 一开 SSH 就报 `remote port forwarding failed for listen port 47654` | 上次 SSH 没干净退出,Linux 侧端口被占 | `ss -tlnp \| grep 47654` 找进程杀掉,或换端口(agent / bridge / ssh config 三处一起改) |

---

## 和 claw-jump 共存

两套完全独立,共用一条 SSH 连接,端口不冲突:

| 组件 | 跑在哪 | 端口 | 用途 |
|---|---|---|---|
| `claw_agent.py` | Windows | `:47653` | toast 通知 + 桌面爪子动画 |
| `buddy_serial_agent.py` | Windows | `:47654` | USB 串口到 M5StickC Plus |
| `claw-jump/linux/hook.sh` | Linux | POST `:47653` | 由 Claude Code `Stop` / `Notification` 触发 |
| `serial_bridge_linux.py` | Linux | `:19191`(给 hook)+ POST `:47654` | 扫 jsonl + 转发 `PreToolUse` |

两个 Windows agent 并列开机启动,Linux 上 bridge 和 hook 都挂在 `~/.claude/settings.json`,
三种事件(`Stop` / `Notification` / `PreToolUse`)各自走各自的链路,互不打扰。

---

## 回退到原 Windows 单机版

如果你**在本地 Windows 上直接用 Claude Desktop**(不经过 SSH),那直接用原项目的
`tools/serial_bridge.py` + `tools/hook_permission.py` 就够了,本文档里的东西一个都不需要。

唯一冲突:**不要同时开**两种模式,端口和 COM 口都会抢。

---

## 本 fork 新增文件一览

| 路径 | 作用 | 跑在哪 |
|---|---|---|
| `windows-agent/buddy_serial_agent.py` | HTTP :47654 + COM 串口 I/O | Windows |
| `windows-agent/requirements.txt` | `pyserial` | Windows |
| `windows-agent/start_buddy_serial.bat` | `pythonw.exe` 后台启动 | Windows |
| `tools/serial_bridge_linux.py` | 扫 jsonl + HTTP :19191/permission + 转发到 agent | Linux |
| `docs/linux-settings.snippet.json` | Claude Code `PreToolUse` hook 片段 | 文档 |
| `README.linux-remote.md` | 本文件 | 文档 |

原项目文件**零改动**(`tools/serial_bridge.py` / `tools/hook_permission.py` / `src/*` / `vscode-buddy/*`),
向后兼容随时可切回。
