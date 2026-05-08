# CAPTCHA Debugging Process Document

## 背景与目标

目标：让 OpenMimi 自动完成 `xft.cmbchina.com` 的登录流程（账号 18584828398 / Liszt123）。该网站在点击登录按钮后会弹出一个滑块拼图验证码（slider/jigsaw CAPTCHA）。

核心难点：验证码需要人类级别的交互才能通过，且每次出现的缺口位置随机。

## 整体思路框架

我的调试方法论遵循"假设→验证→记录→迭代"的循环：

1. **观察现象** — 先让代码跑起来，看实际行为
2. **提出假设** — 基于现象猜测失败原因
3. **设计最小实验** — 写一个针对性脚本验证单一假设
4. **分析结果** — 根据输出确认或排除假设
5. **更新认知** — 将发现写入文档，指导下一次假设

## 各阶段详细过程

### 第一阶段：理解 CAPTCHA 结构

**做了什么：**
- 用 `agent_browser` 的 `eval` 动作执行 JavaScript，提取 DOM 元素信息
- 获取了背景图、拼图块、滑块手柄、轨道的尺寸和位置

**发现：**
- 背景图 `.bottomImage`：340x278px
- 拼图块 `.dragImage`：78x278px，初始与背景左对齐
- 手柄 `.imageVerifyDragButton`：60x40px，在轨道左侧
- 轨道 `.imageVerifyDrag`：340x40px

**关键洞察：** 手柄在拼图块下方（Y 坐标不同），但拖动手柄会联动移动拼图块。

### 第二阶段：为什么 CDP 合成事件不行？

**假设：** 用浏览器的 click/drag API（基于 CDP）可以直接操作滑块。

**验证：** 执行 CDP 合成的 drag 后，手柄 left 仍为 0px，没有移动。

**分析：** 现代网站的验证码会检查 `event.isTrusted`。CDP 注入的事件 `isTrusted=false`，被 JavaScript 忽略。

**结论：** 必须使用操作系统级别的真实鼠标事件。

### 第三阶段：SendInput 结构对齐危机

**假设：** Windows `SendInput` API 可以生成 `isTrusted=true` 的鼠标事件。

**验证：** 第一次调用后，鼠标光标完全没有移动。

**排查过程：**
1. 怀疑坐标转换错误 — 添加了 DPI awareness，确认坐标系统一
2. 怀疑 SendInput 调用失败 — 检查返回值，发现成功但无效
3. 怀疑结构体对齐 — 用 `ctypes.sizeof(_INPUT)` 检查，发现是 32 字节
4. **重大突破：** Windows x64 的 `INPUT` 结构体应该是 40 字节！`MOUSEINPUT` 中 `dwExtraInfo` 是 `ULONG_PTR`（8 字节），而旧代码用了 `ctypes.c_ulong * 7` 导致 union 只有 28 字节，整个 `INPUT` 只有 32 字节。
5. 修复：正确定义 `_MOUSEINPUT`、`_KEYBDINPUT`、`_HARDWAREINPUT` 和 `_INPUT` 结构体，确保 `dwExtraInfo` 为 `ctypes.c_ulonglong`。

**结论：** 结构体对齐是静默失败——SendInput 返回成功，但 Windows 内部因错位读取到 `dwFlags=0` 而忽略所有事件。

### 第四阶段：为什么快拖不行？

**假设：** 修复 SendInput 后，标准 20 步、10ms 延迟的 drag 应该能工作。

**验证：** 手柄移动了 150px，但拼图块纹丝不动（left=0px）。

**分析：**
- 尝试了多种目标：手柄中心、拼图中心、轨道中心
- 只有拖动手柄时，手柄本身会移动
- 拖拼图或轨道会导致全部重置到 0px

**假设升级：** 验证码的 JavaScript 事件处理需要更细粒度、更慢速的鼠标移动才能正确跟踪拖拽状态。

**验证：** 将 steps 从 20 提高到 80，delay_ms 从 10 提高到 25（总时间 ~2 秒）。

**结果：** 手柄 150px，拼图 139.858px —— 两者都动了！

**结论：** 验证码的事件处理器有频率限制或状态机，快速拖拽（200ms）跟不上，慢速拖拽（2s）可以正确同步。

### 第五阶段：异步验证假说

**假设：** 拖动后验证码需要几秒进行服务器端异步验证，我们应该等待而不是立即判定失败。

**验证：** `debug_captcha_wait.py` 拖动 150px 后，每秒轮询 DOM 状态，持续 10 秒。

**结果：**
- t=1s：手柄 150px，拼图 139.858px
- t=2s：手柄 0px，拼图 0px —— 已自动重置

**结论：** 验证是同步的（1-2 秒内完成），重置是验证码失败后的自动行为，不是异步延迟。

### 第六阶段：暴力搜索的陷阱

**假设：** 用暴力搜索测试 50-260px 的各种距离，应该能找到正确答案。

**验证：** `debug_captcha_brute.py` 依次测试 50, 70, 90, ..., 260px，每次失败后拖回 0px。

**结果：** 全部失败。

**事后分析：**
1. **登录不稳定：** 有时登录按钮找不到，导致测试中断
2. **间距太粗：** 20px 步进可能错过了真实答案
3. **缩放因子未知：** 当时还不知道手柄移动和拼图移动的换算关系
4. **自动重置干扰：** 验证码失败后 1-2 秒自动重置，脚本中的"拖回 0px"可能与新验证码冲突

### 第七阶段：直接 JS 操控

**假设：** 既然慢速拖拽能移动拼图，也许可以直接设置 CSS `left` 属性来跳过拖拽过程。

**验证：** `debug_captcha_js_set.py` 设置 `btn.style.left` 和 `drag.style.left`，并派发 mouseup/change 事件。

**结果：** 所有位置（100-260px）都保持验证码 modal 打开状态。

**结论：** 验证码不仅检查最终位置，还依赖真实的鼠标事件序列（mousedown → mousemove → mouseup）。仅靠 CSS 和合成事件无法欺骗验证逻辑。

### 第八阶段：OpenCV 图像分析

**假设：** 从 DOM 提取背景图和拼图块，用 OpenCV 模板匹配可以找到缺口位置。

**验证：**
1. 提取 `data:image/png;base64,...` 格式的背景图和拼图块
2. 尝试多种方法：灰度模板匹配、边缘匹配、亮像素检测、角点检测

**结果：**
- 模板匹配找到了拼图块的**当前位置**（x=45），不是缺口
- 边缘匹配置信度极低（0.10）
- 背景图是一张完整照片，没有明显的"空洞"

**关键发现：** 缺口不是背景图的一部分，而是客户端通过 CSS 渲染的（可能是边框覆盖或蒙版）。OpenCV 分析原始图片资产无法定位缺口。

### 第九阶段：坐标精度验证

**假设：** 鼠标可能没有精准落在手柄上，导致验证码忽略拖拽。

**验证：**
1. 计算手柄屏幕坐标
2. 移动鼠标到手柄中心
3. 用 `elementFromPoint` 检查鼠标下的元素

**结果：**
- `elementFromPoint` 返回 SPAN（手柄的子元素），不是手柄 DIV 本身
- 但执行 10px 的 mini drag 后，手柄成功移动

**结论：** 鼠标落在手柄内部的子元素上，事件通过冒泡机制被手柄捕获。坐标精度不是问题。

### 第十阶段：手柄-拼图缩放因子（关键发现）

**观察：** 10px 手柄拖动 → 拼图移动 9.32px；150px 手柄拖动 → 拼图移动 139.86px。

**推导：**
- 手柄可移动范围 = 轨道宽 - 手柄宽 = 340 - 60 = **280px**
- 拼图可移动范围 = 背景宽 - 拼图宽 = 340 - 78 = **262px**
- 缩放因子 = 280 / 262 = **1.0687**

**意义：** 如果缺口在背景图的 x=200px 处（相对于背景左边缘），拼图需要移动 200px，而手柄需要移动 200 × 1.0687 = **213.7px**。

这解释了为什么 200px 的暴力搜索失败——它只把拼图移动了 187px，还差 13px。

## 当前瓶颈

**已知：**
- 拖拽机制完全正确（SendInput、慢速、80步、25ms延迟）
- 坐标转换正确
- 缺口位置每次随机

**未知：**
- 如何可靠地获取每个 CAPTCHA 实例的缺口精确位置

**候选方案：**
1. **LLM Vision：** 让视觉模型分析截图，估计缺口位置（已写脚本，待稳定运行）
2. **渲染层分析：** 用 JavaScript 检查渲染后的像素差异，找到 CSS 绘制的缺口边框
3. **网络拦截：** 拦截验证码加载时的 API 响应，看服务器是否返回了缺口坐标

## 调试工具清单

| 脚本 | 用途 |
|------|------|
| `debug_captcha_wait.py` | 测试异步验证假说 |
| `debug_captcha_brute.py` | 粗粒度暴力搜索 |
| `debug_captcha_fine.py` | 细粒度（5px）暴力搜索 |
| `debug_captcha_gap.py` | OpenCV 图像分析 |
| `debug_captcha_hover.py` | 坐标精度验证 |
| `debug_captcha_js_set.py` | 直接 JS 位置操控 |
| `debug_captcha_screenshot.py` | 捕获 CAPTCHA 截图和原始图片 |
| `debug_captcha_state.py` | 检查 React 组件内部状态 |
| `analyze_captcha_images.py` | 本地 OpenCV 分析已保存的图片 |
| `solve_captcha_vision.py` | LLM Vision 缺口估计 + 自动拖拽 |

## 关键代码修改

### computer.py — SendInput 结构体修复

```python
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulonglong),  # 8 bytes on x64
    ]  # 32 bytes
```

### computer.py — DPI Awareness

```python
ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PerMonitorV2
```

### computer.py — 可配置拖拽延迟

```python
delay_ms = min(inp.get("delay_ms", 10), 500)  # 默认 10ms，上限 500ms
```

### loop.py — CAPTCHA 专用系统提示

添加了慢速拖拽指导（steps=80, delay_ms=25）、坐标转换公式、视觉分析指导。

## 经验教训

1. **不要假设库代码正确：** `ctypes` 结构体定义必须严格匹配平台 ABI，一个字段大小错误会导致完全静默的失败。

2. **单一变量原则：** 每次测试只改变一个参数（如拖拽速度、目标元素、距离），否则无法归因。

3. **区分"能移动"和"能验证"：** 手柄移动只证明事件被接收，不代表验证通过。必须观察验证码 modal 是否消失。

4. **验证码是状态机：** 不是简单的"最终位置检查"，而是对事件序列、速度、轨迹的综合判断。

5. **截图分析 vs 原始资产：** 对于客户端渲染的验证码，分析 DOM 截图比分析原始图片资产更有用。

## 下一步行动

1. 稳定运行 `solve_captcha_vision.py`，获取第一个 LLM Vision 估计的成功案例
2. 如果 Vision 精度不足（±10px 误差），尝试结合缩放因子后做 ±5px 微调搜索
3. 探索 JavaScript 拦截方案：能否读取验证码组件的 props/state 获取缺口坐标
4. 一旦单实例 CAPTCHA 可解，整合到 `test_xft_login_loop.py` 做端到端测试
