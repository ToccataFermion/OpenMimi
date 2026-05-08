# Camoufox 与反检测浏览器调研（2026-05）

## 调研目的

评估 Camoufox 作为 OpenMimi 浏览器自动化反检测增强的集成可行性，同时调研 2026 年主流反检测浏览器自动化工具的现状。

---

## 1. Camoufox 现状

### 1.1 维护状态

| 项目 | 详情 |
|------|------|
| 原作者 | daijro，2025 年因个人原因退出 |
| 新管理团队 | Clover Labs |
| 维护空窗期 | 约 1 年（2024-2025） |
| 当前状态 | **活跃开发中**，但明确标记为 **高度实验性** |
| 稳定性 | 2026 年最新版本为预览质量，存在破坏性变更 |

> 官网声明："Camoufox is under active development to get back to its original performance. The latest releases are highly experimental (expect breaking changes)."

### 1.2 技术架构（核心优势）

Camoufox 的核心差异在于 **引擎级 C++ 补丁** vs JS 运行时补丁：

| 检测点 | Camoufox（C++ 级） | 常规方案（JS 补丁） |
|--------|-------------------|-------------------|
| `navigator.webdriver` | 编译级返回 `false` | JS property 覆盖（可被探测） |
| Canvas/WebGL | 原生引擎伪装 | 页面加载后 JS 注入 |
| 指纹一致性 | 内部对齐（OS/GPU/屏幕/时区） | 经常不匹配 |
| TLS 指纹 | 真实 Firefox 栈 | Chrome 栈或模拟 |
| 检测抗性 | 高 — 无 shim 层可探测 | 中 — 补丁可被发现 |

### 1.3 当前问题

- Firefox 基版本落后，跟不上最新版
- 指纹不一致性问题被新发现
- 对现代检测系统（DataDome、Cloudflare）效果下降

### 1.4 社区生态

| 项目 | 说明 | 状态 |
|------|------|------|
| `redf0x1/camofox-browser` | TypeScript REST API 封装，面向 AI Agent | 2026 预览版 |
| `@askjo/camoufox-browser` | NPM 包，headless browser server | 2026 已发布 |
| `DenimEvert/camoufox-capsolver` | Camoufox + CapSolver 集成 | 2025 晚期活跃 |

**关键发现**：没有出现核心的 **硬分叉**（hard fork），生态以封装和集成项目为主。

### 1.5 结论

| 场景 | 建议 |
|------|------|
| 立即生产使用 | 不建议，考虑替代方案 |
| 现有 Camoufox 部署 | 锁定到最后已知稳定版本，谨慎观望 Clover Labs |
| 研究/实验 | 可行，关注恢复进展 |
| AI Agent 集成 | 社区封装项目活跃但质量为预览级 |

---

## 2. 2026 年反检测工具全景

### 2.1 主流工具对比

| 工具 | 基础框架 | 状态 | 核心特性 |
|------|---------|------|---------|
| **Rebrowser** | Puppeteer/Playwright | 活跃开源 | 修复 `Runtime.enable` CDP 检测漏洞 |
| **Patchright** | Playwright | 活跃开源 | Playwright 即插即用替代 |
| **Puppeteer Real Browser** | Puppeteer | 2026-02 停止维护 | 曾综合多种隐身技术 |
| **Camoufox** | Firefox | 恢复中/实验性 | 指纹注入、WebGL 伪装 |
| **Puppeteer Stealth** | Puppeteer | 活跃 | 模块化规避插件 |
| **undetected-chromedriver** | Selenium | 活跃 | 修改版 ChromeDriver |
| **SeleniumBase** | Selenium | 活跃 | 完整反机器人框架 |

### 2.2 Rebrowser（最值得关注）

**GitHub**: `rebrowser/rebrowser-patches`

- **开源** 的 Puppeteer/Playwright 补丁
- 解决关键的 `Runtime.enable` CDP 检测问题
- 提供即插即用替代包：
  - `rebrowser-puppeteer` / `rebrowser-puppeteer-core`
  - `rebrowser-playwright` (Python + Node.js)
- 包含 bot detector 测试套件 (`rebrowser-bot-detector`)

**核心创新**：`Runtime.enable` 补丁修复了大多数隐身插件遗漏的**根本性 CDP 检测向量**。

### 2.3 Patchright

- **开源** 的 Playwright 改进版
- Python 和 Node.js 均可用
- 主要修改：
  - `navigator.webdriver = false`
  - `HeadlessChrome` UA → 标准 Chrome UA
  - 禁用弹窗拦截
- **局限**：对高级反机器人（Cloudflare、DataDome）仍不可靠

---

## 3. 对 OpenMimi 的启示

### 3.1 当前架构评估

OpenMimi 使用 **vercel-labs/agent-browser (Rust CLI) + CDP**，与 Playwright/Puppeteer 架构不同：

- **优势**：Rust CLI 直接控制 Chrome，不经过 Node.js 层，部分 CDP 检测向量天然不存在
- **风险**：仍需确认 Rust CLI 是否使用了 `Runtime.enable` 等可被检测的 CDP 命令

### 3.2 建议行动

1. **短期**：继续优化现有 JS 级反检测（stealth 注入、human_scroll、slow_mo、wander）
2. **中期**：评估是否能对 agent-browser 启动的 Chrome 应用 Rebrowser 补丁
3. **长期**：如 Camoufox 恢复稳定，评估集成可行性；或考虑 Patchright 作为备选方案

### 3.3 关键检测向量清单

应优先排查 OpenMimi 当前是否暴露以下检测点：

- [ ] `Runtime.enable` CDP 命令使用痕迹
- [ ] `navigator.webdriver` 返回值
- [ ] Chrome DevTools Protocol 特征指纹
- [ ] `window.chrome` / `window.chrome.runtime` 异常
- [ ] Canvas/WebGL 指纹一致性

---

## 4. 参考链接

- [Camoufox 官网](https://camoufox.com/)
- [daijro/camoufox GitHub](https://github.com/daijro/camoufox)
- [Camoufox with JavaScript 教程](https://bytetunnels.com/posts/camoufox-with-javascript-browser-automation-without-detection/)
- [Camoufox vs Selenium 对比](https://bytetunnels.com/posts/camoufox-vs-selenium-anti-detection-approaches-compared/)
- [Rebrowser Patches](https://rebrowser.net/docs/patches-for-puppeteer-and-playwright)
- [Patchright 教程](https://www.zenrows.com/blog/patchright)
- [2026 反检测技术综合指南](https://www.browserless.io/blog/anti-detection-techniques-2026-guide)
- [Camoufox 替代方案对比](https://multilogin.com/blog/camoufox-alternatives/)
