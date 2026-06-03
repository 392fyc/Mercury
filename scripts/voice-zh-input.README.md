# voice-zh-input — 外围中文语音输入 (#465)

给 Claude Code TUI(及任意聚焦文本框)加**中文**语音输入。
绕开原生 `/voice` —— 后者服务端写死 20 语言、**无中文**。本地运行,音频不出本机。

**两种模式**(`VOICE_ZH_MODE`):
- **连续模式(默认)**:常驻监听,能量 VAD 自动分句。直接说话,每句(说话到一段静音为止)
  自动转写并注入,短暂确认窗口后自动回车。`Ctrl+Alt+P` 暂停/恢复。
- **推键模式(toggle)**:按热键开始录音,再按一次停止。

## 为什么是「外围」+ 剪贴板注入

- **原生 /voice 无中文**:语言列表服务端硬编码,无 `zh`。
- **注入机制 = 剪贴板 + Ctrl+V**:Windows Terminal/ConHost 对模拟 Unicode 键击乱码
  ([microsoft/terminal#12977](https://github.com/microsoft/terminal/issues/12977));
  而 TUI 通过 bracketed paste 正确接收粘贴的中文。剪贴板承载 Unicode,Ctrl+V 只是
  ASCII 级虚拟键(不触发 Unicode bug)。逐字键击 / stdin / IPC 注入均不可行。
- 架构 B(`scripts/` 自研轻量粘合),实证选定见 Issue #465。

## 安装

```bash
# 1) 建专用 venv(Python 3.9+;实测 3.14.3)
uv venv .venv-voice --python 3.14

# 2) 核心依赖(CPU 开箱即用)
uv pip install --python .venv-voice -r requirements-voice-zh.txt

# 3) (可选)GPU 加速 —— RTX 4060 实测 1.8s/句 vs CPU 13.7s/句
uv pip install --python .venv-voice nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" \
    nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12
```

首次运行会从 HuggingFace 下载 `large-v3` 模型(约 3GB),之后走本地缓存。

## 用法

```bash
.venv-voice/Scripts/python.exe scripts/voice-zh-input.py
```

### 连续模式(默认)

1. 启动后会先校准 ~1 秒环境噪声(**保持安静**),打印 `vad_threshold`。
2. **直接说话** —— 说完停顿 ~0.8 秒即视为一句,自动转写并粘进当前聚焦的输入框。
3. 粘贴后约 2 秒确认窗口,期间不打断就**自动回车发送**;想取消发送/编辑就按 `Ctrl+Alt+P` 暂停。
4. `Ctrl+Alt+P` 暂停/恢复监听;`Ctrl+Alt+Q` 退出。连续模式默认**不响 beep**(可设 `VOICE_ZH_CONT_BEEP=1` 开启语音起止提示音)。

> 关掉自动回车:`VOICE_ZH_AUTO_ENTER=0`(只粘不发送)。漏字/分句太碎:把麦克风电平拉高,
> 或降低 `VOICE_ZH_VAD_THRESH`、调大 `VOICE_ZH_SILENCE_SEC`。

### 推键模式(toggle)

```bash
$env:VOICE_ZH_MODE="toggle"; .venv-voice/Scripts/python.exe scripts/voice-zh-input.py
```
按 `Ctrl+Alt+Space` 开始录音,再按一次停止 → 转写粘到输入框(无自动回车)。`Ctrl+Alt+Q` 退出。

独立运行,与 Claude Code 解耦 —— 在任意聚焦的文本框都能用。建议**另开一个终端窗口**常驻运行,在 Claude Code 窗口里说话。

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|------|------|------|
| `VOICE_ZH_MODE`   | `continuous` | `continuous`(VAD 自动分句)/ `toggle`(推键录音) |
| `VOICE_ZH_PAUSE`  | `ctrl+alt+p` | 连续模式暂停/恢复热键 |
| `VOICE_ZH_AUTO_ENTER` | `1` | 连续模式每句确认窗口后自动回车;`0` 只粘不发送 |
| `VOICE_ZH_GRACE_SEC` | `2.0` | 自动回车前的确认窗口秒数(期间按暂停可取消) |
| `VOICE_ZH_SILENCE_SEC` | `0.8` | 尾随静音多少秒判定一句结束 |
| `VOICE_ZH_MIN_SEC` | `0.4` | 短于此秒数的片段忽略(防误触) |
| `VOICE_ZH_VAD_THRESH` | `auto` | 能量 VAD 阈值;`auto` 按启动校准的噪声底自适应,或填固定 float |
| `VOICE_ZH_ONSET_BLOCKS` | `3` | 需连续多少块(~50ms/块)语音才判定起音(防噪声尖峰误触) |
| `VOICE_ZH_NOSPEECH_MAX` | `0.6` | 段 `no_speech_prob` ≥ 此值丢弃(滤非语音/幻觉) |
| `VOICE_ZH_LOGPROB_MIN` | `-1.0` | 段 `avg_logprob` ≤ 此值丢弃(滤低置信幻觉) |
| `VOICE_ZH_CONT_BEEP` | `0` | `1` 开启连续模式语音起止提示音 |
| `VOICE_ZH_HOTKEY` | `ctrl+alt+space` | 推键模式录音热键(避开原生 /voice 占用的 Space) |
| `VOICE_ZH_QUIT`   | `ctrl+alt+q` | 退出热键(避开 TUI 常用的 Esc) |
| `VOICE_ZH_MODEL`  | `large-v3` | faster-whisper 模型(可改 `medium`/`small` 降显存/提速) |
| `VOICE_ZH_DEVICE` | `auto` | `auto`(cuda→cpu 回退) / `cuda` / `cpu`(非法值会启动报错并列出允许值) |
| `VOICE_ZH_BEEP`   | `1` | `0` 关闭提示音 |
| `VOICE_ZH_PASTE`  | `1` | `1` 自动 Ctrl+V 粘贴;`0` 仅复制到剪贴板(安全模式,手动粘贴) |
| `VOICE_ZH_MAX_SEC`| `120` | 单次录音内存上限秒数,超出丢弃多余音频并告警(防 RAM 无界) |
| `VOICE_ZH_VAD`    | `0` | `1` 开启 Silero VAD 静音裁剪;默认关(VAD 对低增益麦克风会过度裁剪成空) |
| `VOICE_ZH_NORMALIZE` | `1` | `1` 对录音做峰值归一化(救低增益麦克风);`0` 关闭 |
| `VOICE_ZH_DEVICE_INDEX` | *(空)* | 强制指定输入设备序号(默认用系统默认输入)。用 `tmp/scan_devices2.py` 找出有信号的序号 |

> **粘贴安全**:`VOICE_ZH_PASTE=1` 时,转写完成(可能数秒后)会把文本 Ctrl+V 进**当时聚焦的任意窗口**。
> 保持目标输入框聚焦,或设 `VOICE_ZH_PASTE=0` 走仅复制模式(手动粘贴),避免误粘到无关窗口/终端/管理员工具。

## 技术要点(#465 实证)

- **STT**:`faster-whisper` 1.2.1 + `large-v3`,显式 `language="zh"`(语言概率 1.00)。
  SAPI 中文样本转写内容 100% 正确(仅标点风格 + 数字归一化 `一二三四五→12345`)。
- **延迟**:GPU `cuda/float16` 约 1.8s/句;CPU `cpu/int8` 约 13.7s/句(均 large-v3)。
- **GPU DLL**:ctranslate2 ≥4.5 需 CUDA 12 + cuDNN 9。脚本自动注册 bundled
  `nvidia/*/bin` 到 DLL 目录**并前置 PATH** —— 仅 `add_dll_directory` 不够,
  ctranslate2 内部加载器走 PATH 才能找到 `cublas64_12.dll`。缺 GPU 库自动回退 CPU。
- **录音**:`sounddevice` InputStream 按设备**原生采样率**录单声道,送内存后线性重采样到 16kHz
  喂 whisper(不落盘)。**强制 16kHz 直采会被 Realtek 驱动返回数字静音(peak 0.0000)而非报错**,
  故必须原生采样率 + 后重采样。默认设备静音时设 `VOICE_ZH_DEVICE_INDEX`(见故障排查)。
- **低增益**:部分笔记本麦克风电平极低(peak ~0.01),默认开启峰值归一化拉到 ~0.3 再转写;
  VAD 默认关(会把低增益语音裁成空)。
- **编码**:脚本须 UTF-8 + `sys.stdout.reconfigure(encoding="utf-8")`;勿用 shell 内联
  `python -c` 传中文(Windows console GBK 编码会乱码)。

## 故障排查

- **热键无反应**:`keyboard` 库的全局钩子在个别 Windows 配置下需管理员权限 —— 用管理员
  终端重跑。发送 Ctrl+V(SendInput)本身不需管理员(已实测)。
- **`cublas64_12.dll not found / cannot be loaded`**:GPU 库没装全。需要
  cublas + cudnn(9.x) + cuda-runtime + cuda-nvrtc 四件套;或设 `VOICE_ZH_DEVICE=cpu` 走 CPU。
- **转写为空 `''` / `peak=0.0000` 静音**:① 先确认麦克风在 Windows 没被静音、电平拉高(设置 → 声音 →
  输入);② 跑 `tmp/scan_devices2.py`(边说边扫)找出标 `<-- SIGNAL` 的输入设备序号,设
  `VOICE_ZH_DEVICE_INDEX=<序号>` 重启 daemon;③ 注意脚本已按原生采样率录音 + 归一化,无需手动改。
- **粘贴落错窗口**:停止录音后转写需 ~2s,期间别切换焦点 —— 保持目标输入框聚焦,或用 `VOICE_ZH_PASTE=0`。
