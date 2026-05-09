# OpenMimi 探索过程文档

## 1. 项目定位

OpenMimi 是一个基于 Anthropic tool_use 协议的本地 Windows AI Agent。核心架构：

- **LLM 驱动循环** (`loop.py`): LLM <-> tool_use <-> tool_result
- **AgentBrowserTool** (`agent_browser.py`): 基于 Rust CLI (vercel-labs/agent-browser) + CDP 的浏览器自动化
- **ComputerTool** (`computer.py`): Windows 桌面级自动化 (mss 截图 + SendInput)
- **工具集合** (`collection.py`): 统一注册和调度

## 2. 新增工具能力

### 3.1 AgentBrowserTool 新增动作（第一批次）

| 动作 | 说明 |
|------|------|
| `stealth` | 注入 JS 隐藏自动化特征 |
| `clear_cache` | 清除 cookies / localStorage / sessionStorage |
| `set_viewport` | 调整浏览器窗口大小 |
| `save_session` | 保存当前页面 URL + cookies + storage 到 JSON |
| `load_session` | 从 JSON 恢复 session |
| `scroll_into_view` | 滚动元素进入视口 |

### 3.2 AgentBrowserTool 新增动作（第二批次）

| 动作 | 说明 |
|------|------|
| `network_log` | JS 拦截 fetch/XHR，记录请求日志 + 响应状态码/Body（用于 API 逆向） |
| `right_click` | 右键点击元素（CDP mouse down/up with right button） |
| `double_click` | 双击元素（CDP 两次 mouse down/up） |
| `network_modify` | 修改网络行为：注入 headers、阻断 URL、mock 响应、覆盖 UA |
| `wait_for_network_idle` | 等待无活跃网络请求持续指定时长（SPA 异步加载友好） |
| `console` | 捕获浏览器 console.log/error/warn/info |
| `pdf` | 保存当前页面为 PDF（CDP printToPDF） |
| `page_source` | 获取当前页面原始 HTML |
| `wait_for_navigation` | 轮询等待 URL 变化（SPA 友好） |
| `visual_locate` | OpenCV 模板匹配：通过截图视觉定位元素（支持自动点击） |
| `extract` 增强 | 结构化提取：headings, links, forms, tables, metadata, images |
| `emulate_device` | 模拟移动设备：iPhone 14, Pixel 7, iPad Mini（CDP + JS fallback） |

### 3.3 AgentBrowserTool 构造函数增强

| 参数 | 说明 |
|------|------|
| `proxy` | `--proxy-server` 路由流量 through 代理 |
| `user_data_dir` | `--user-data-dir` 完整 profile 持久化（IndexedDB, cache, extensions） |
| `stealth` | 启用 14 项反检测 JS 注入 |
| `slow_mo_ms` | 每次动作后插入随机延迟（±20% jitter），模拟人类操作节奏 |
| `wait_for_network_idle` | 等待网络空闲（SPA 异步加载完成检测） |

### 3.4 ComputerTool 新增动作

| 动作 | 说明 |
|------|------|
| `file` (read/write) | 磁盘文件读写 |
| `get_screen_info` | 返回主显示器分辨率 + DPI |
| `shell` | 执行 shell 命令（带超时） |
| `focus_window` 增强 | 返回窗口 rect (left, top, width, height) |
| `ocr` | Tesseract OCR 提取屏幕区域文字（支持 chi_sim+eng） |
| `click_text` | OCR 识别屏幕文字并自动点击（无需坐标） |
| `window_manage` | 窗口管理：move, resize, minimize, maximize, restore, close |
| `type` 增强 | 自动检测 Unicode/中文，无 VK 映射时改用剪贴板粘贴 |

### 3.5 Loop 系统提示词增强

- 常见登录与滑块验证码的通用操作说明（站点无关）
- CAPTCHA 缩放系数和视觉分析指南
- 所有新工具的能力说明
- 工具超时配置（`OPENMIMI_TOOL_TIMEOUT_S`）

## 4. 安全修复

- 曾发现部分脚本硬编码测试凭据；已改为环境变量或移除演示脚本
- 历史提交中可能仍保留敏感信息，部署前请轮换凭据并审计仓库历史

## 5. 脚本与仓库整理

- 站点专用的验证码/登录演示脚本已从本仓库移除，避免与通用 Agent 代码耦合；集成测试请放在自有仓库或 CI 私密配置中。

## 6. 新增工具能力（第三批次，2026-05-08）

### 4.1 AgentBrowserTool

| 动作 | 说明 |
|------|------|
| `react_fill` | React/Vue 受控输入专用填充：HTMLInputElement.prototype.value setter + dispatchEvent(input/change) |
| `get_url` | 返回当前页面 URL（无需 eval） |
| `get_title` | 返回当前页面 title（无需 eval） |
| `cdp` | 直接发送任意 Chrome DevTools Protocol 命令（escape hatch） |
| `key_combo` | CDP 级多键同时按下（如 ['Control','a'] 全选） |

### 4.2 CDP 深度集成

- **Cookie 操作全面 CDP 化**：`storage_action` 的 get/set/delete/clear 均优先使用 `Network.*` CDP API
  - `getAllCookies`：读取含 HTTP-only 的完整 cookie jar
  - `setCookie`：按当前页面 URL 写入 cookie
  - `deleteCookies`：按名称删除单条 cookie
  - `clearBrowserCookies`：清空所有 cookie
- **Session 持久化 CDP 化**：`save_session` / `load_session` 自动检测 cookie 格式
  - 保存时优先导出 CDP 结构化数组（含 domain/path/expires）
  - 加载时若检测到结构化数组，使用 `Network.setCookie` 逐条恢复
  - 若检测到旧版字符串，回退到 `document.cookie` 保持兼容

### 4.3 ComputerTool

| 动作 | 说明 |
|------|------|
| `click_image` | OpenCV 模板匹配找到屏幕图像并点击其中心 |

## 7. 新增工具能力（第四批次，2026-05-08）

### 5.1 ComputerTool

| 动作 | 说明 |
|------|------|
| `mouse_move` humanize | 新增 `humanize=true` 参数，使用 Bezier 轨迹 + 加减速曲线模拟人类鼠标移动，替代瞬间跳跃 |
| `mouse_click` wander | 新增 `wander=true` 参数，点击前在目标附近进行 2-4 次随机微动，模拟人类瞄准行为，增强反检测 |
| `batch` | 批量执行多个桌面动作（`steps` 数组），`bail` 控制遇错是否中断，减少 LLM 往返 |

### 5.2 AgentBrowserTool

| 动作 | 说明 |
|------|------|
| `get_attribute` | 读取元素 DOM 属性（href, src, data-* 等），支持 ref 或 target_text 定位 |
| `set_attribute` | 写入元素 DOM 属性，支持 ref 或 target_text 定位 |
| `get_property` | 读取元素 JS 属性（value, checked, innerText, innerHTML 等） |
| `set_timezone` | CDP `Emulation.setTimezoneOverride` 覆盖浏览器时区 |
| `set_locale` | CDP `Emulation.setLocaleOverride` 覆盖浏览器语言环境 |
| `set_geolocation` | CDP `Emulation.setGeolocationOverride` 覆盖 GPS 定位（省略坐标则清除） |
| `scroll_until` | 分步滚动直到元素或文本出现，适用于无限滚动页面和长表单 |

### 5.3 Bug 修复

- `click_image`：修复调用不存在的方法 `_mouse_click`，改为正确的 `mouse_move` + `SendInput`
- `click_text`：修复调用不存在的 `_send_mouse_move` / `_send_mouse_click`，改为正确的异步流程

### 5.4 调研：2026 年反检测浏览器工具全景

- 完成 Camoufox 及替代方案调研，详细报告见 `docs/camoufox_research_2026.md`
- **关键发现**：Rebrowser 的 `Runtime.enable` CDP 补丁是当前最具工程价值的反检测方案；Camoufox 处于重建期，尚未恢复生产级稳定性

## 8. 脚本与工具链

- 演示用登录脚本已迁出本仓库；`react_fill` 等能力以源码与单元测试为准。

## 9. 下一步方向

### 9.1 高优先级

1. **测试验证** - 扩充 `tests/` 覆盖关键浏览器与桌面路径
2. **Vision-based 元素定位** - 减少对 DOM 的依赖，用 VLM 理解页面视觉布局
3. **AI 驱动的自愈选择器** - 元素变更时自动找到等效元素

### 9.2 中优先级

4. **行为级反检测增强** - 模拟人类阅读模式（滚动停顿、鼠标徘徊）
5. **Camoufox 集成评估** - 已完成初步调研（见 `docs/camoufox_research_2026.md`）。结论：Camoufox 2026 年处于 Clover Labs 接管后的重建期，仍为实验性；更值得关注的是 Rebrowser（修复 `Runtime.enable` CDP 检测）和 Patchright（Playwright 即插即用替代）
6. **Screen region actions** - 对 OCR 识别的区域直接执行点击/输入（click_image 已覆盖视觉场景）

### 9.3 长期

7. **多模态理解** - 结合截图 + DOM + OCR 进行联合推理
8. **自动重试与恢复** - 智能检测失败原因并自动调整策略重试
