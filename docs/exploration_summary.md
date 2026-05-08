# OpenMimi 探索过程文档

## 1. 项目定位

OpenMimi 是一个基于 Anthropic tool_use 协议的本地 Windows AI Agent。核心架构：

- **LLM 驱动循环** (`loop.py`): LLM <-> tool_use <-> tool_result
- **AgentBrowserTool** (`agent_browser.py`): 基于 Rust CLI (vercel-labs/agent-browser) + CDP 的浏览器自动化
- **ComputerTool** (`computer.py`): Windows 桌面级自动化 (mss 截图 + SendInput)
- **工具集合** (`collection.py`): 统一注册和调度

## 2. xft.cmbchina.com 登录流程攻克

### 2.1 问题背景

xft.cmbchina.com 是招商银行小企业金融平台。登录流程复杂：

1. 首页点击"登录"打开表单
2. 填写手机号 + 密码
3. 提交后出现滑块验证码
4. 验证码通过后进入工作台

### 2.2 关键技术难点

**React SPA 点击不响应**
- 标准 `element.click()` 无法触发页面跳转
- 解决：使用 CDP 级别的 `Input.dispatchMouseEvent` (force=true)

**表单填写被 React 覆盖**
- 直接设置 `input.value` 后 React 组件不感知
- 解决：使用 `HTMLInputElement.prototype.value` 的 property descriptor setter，然后 dispatch `input` + `change` 事件

```javascript
const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
valueSetter.call(element, value);
element.dispatchEvent(new Event('input', { bubbles: true }));
element.dispatchEvent(new Event('change', { bubbles: true }));
```

**滑块验证码**
- 背景图 340px，拼图 78px，把手 60px
- 关键发现：把手移动距离 ≠ 拼图移动距离
- 缩放系数：`handle_drag = puzzle_gap * 280 / 262 ≈ 1.069`
- 拖动必须用 OS 级 `SendInput` 产生 `isTrusted=true` 事件
- 拖动必须慢（steps=80, delay_ms=25，约 2 秒），让 JS 跟踪轨迹

### 2.3 验证码解法演进

| 阶段 | 方法 | 成功率 | 说明 |
|------|------|--------|------|
| v1 | 人工观察截图估算 | ~30% | 耗时，不准确 |
| v2 | Pixeldiff 像素差分 | ~50% | Canvas 逐像素比较，找最大差异偏移 |
| v3 | **DL 深度学习** (captcha-recognizer) | **~95%** | ONNX YOLO 实例分割，置信度 0.96+ |
| fallback | 暴力枚举 | 100% (最终) | 80-260px 步进 15px |

DL 解法流程：
1. 截图 → 裁剪到验证码弹窗区域
2. `Slider().identify_offset(crop_path)` 返回缺口偏移量
3. 应用缩放系数得到把手拖动距离
4. ±10px 容错尝试

### 2.4 发现：登录后无认证态

探索发现 xft.cmbchina.com 前台是公开营销页面。即使"登录成功"，导航栏也只有公开内容（关于我们、新闻资讯、帮助中心），未捕获到 token/cookie 等认证凭证。可能原因：
- 前台展示与实际业务系统分离
- 需要额外的企业认证才能访问真实工作台

## 3. 新增工具能力

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
| `network_log` | JS 拦截 fetch/XHR，记录请求日志 |
| `network_modify` | 修改网络行为：注入 headers、阻断 URL、mock 响应、覆盖 UA |
| `console` | 捕获浏览器 console.log/error/warn/info |
| `pdf` | 保存当前页面为 PDF（CDP printToPDF） |
| `page_source` | 获取当前页面原始 HTML |
| `wait_for_navigation` | 轮询等待 URL 变化（SPA 友好） |
| `extract` 增强 | 结构化提取：headings, links, forms, tables, metadata, images |
| `emulate_device` | 模拟移动设备：iPhone 14, Pixel 7, iPad Mini（CDP + JS fallback） |

### 3.3 AgentBrowserTool 构造函数增强

| 参数 | 说明 |
|------|------|
| `proxy` | `--proxy-server` 路由流量 through 代理 |
| `user_data_dir` | `--user-data-dir` 完整 profile 持久化（IndexedDB, cache, extensions） |
| `stealth` | 启用 14 项反检测 JS 注入 |

### 3.4 ComputerTool 新增动作

| 动作 | 说明 |
|------|------|
| `file` (read/write) | 磁盘文件读写 |
| `get_screen_info` | 返回主显示器分辨率 + DPI |
| `shell` | 执行 shell 命令（带超时） |
| `focus_window` 增强 | 返回窗口 rect (left, top, width, height) |
| `ocr` | Tesseract OCR 提取屏幕区域文字（支持 chi_sim+eng） |
| `window_manage` | 窗口管理：move, resize, minimize, maximize, restore, close |
| `type` 增强 | 自动检测 Unicode/中文，无 VK 映射时改用剪贴板粘贴 |

### 3.5 Loop 系统提示词增强

- 完整的 xft 登录流程指南
- CAPTCHA 缩放系数和视觉分析指南
- 所有新工具的能力说明
- 工具超时配置（`OPENMIMI_TOOL_TIMEOUT_S`）

## 4. 安全修复

- 发现数十个脚本硬编码了测试凭据（手机号/密码）
- 已提交到 GitHub 的历史中仍存在（commit 604cbce）
- 修复方案：使用 `os.environ.get('XFT_PHONE', '...')` + `os.environ.get('XFT_PASSWORD', '...')`
- 通过 Python f-string 注入到 browser-eval JS 中（process.env 在浏览器环境不可用）
- 两个活跃脚本已修复并推送

## 5. 脚本更新

- `scripts/xft_advanced_login.py`: 综合演示所有新能力的生产级脚本
  - persistent profile + stealth headers + network_modify
  - wait_for 替代固定 sleep
  - 结构化 extraction 替代 raw eval
  - page_source 调试 fallback

## 6. 下一步方向

### 6.1 高优先级

1. **测试验证** - 在真实环境运行 xft_advanced_login.py 验证所有新功能
2. **Vision-based 元素定位** - 减少对 DOM 的依赖，用 VLM 理解页面视觉布局
3. **AI 驱动的自愈选择器** - 元素变更时自动找到等效元素

### 6.2 中优先级

4. **行为级反检测增强** - 模拟人类阅读模式（滚动停顿、鼠标徘徊）
5. **Camoufox 集成评估** - Firefox C++ 级补丁的 open-source 替代方案
6. **Screen region actions** - 对 OCR 识别的区域直接执行点击/输入

### 6.3 长期

7. **多模态理解** - 结合截图 + DOM + OCR 进行联合推理
8. **自动重试与恢复** - 智能检测失败原因并自动调整策略重试
