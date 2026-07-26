# newsgrab Design

- 状态：已批准，待写实现计划
- 日期：2026-07-26
- 范围：Google News 采集后端 + 双容器基础设施（Playwright 浏览器服务 + 采集编排服务），可插拔多后端接口

## 1. 背景

`newsgrab` 是一个全新的独立项目，定位是可被多个项目复用的通用新闻/内容采集服务，不隶属于任何单一业务项目。

起因：用户在 `daily_stock_analysis`（股票分析系统）里完成了一个 Google News 采集插件（`plugins/google_news_collector/`，进程内 import 方式），已完整测试+审查通过，但审查过程中发现进程内集成本质上无法彻底解决依赖环境共享风险（`newspaper3k`/`newspaper4k` 包名冲突、全局 `socket` 超时状态污染等），只能不断打补丁应对。用户决定改为完全独立的服务，主项目通过 API 调用，不再进程内 import 任何采集代码。该分支（`feat/google-news-collector-plugin`）暂不合入 `daily_stock_analysis`。

用户另有一个已验证的项目 `newshub`（`/home/ubuntu/workspace/newshub_workspace/newshub`），其中：
- `articlehub` 的采集管道（gnews 查询 + Browserless 跳转解析 + 内容解析 fallback）验证了"四路→三路内容解析 + SSRF 防护"这套逻辑的可用性——本次**参考思路重写**，不直接照搬代码，以适配新架构（跳转解析这次交给 `playwright-service`，不再需要在采集逻辑里自己 monkeypatch gnews 的 URL 处理）。
- `newsbot/browser/`（一个独立的浏览器自动化容器封装）验证了"Xvfb + Chromium + `playwright_stealth` + 手动覆盖 `navigator.webdriver`/`plugins`/`languages` + 特定 Chrome flags（如 `--disable-blink-features=AutomationControlled`）"这套反检测配置真实有效——用户在 newshub 引入 Browserless/这套配置后文章生成量大幅提升。**这套"让浏览器看起来像真人"的配方是本次要直接搬过来复用的核心资产**，而不是 `newsbot` 的容器拆分方式本身（`newsbot` 用的是"每任务动态起一次性容器"的池模型，那是为多用户隔离设计的，newsgrab 不需要）。
- `newsbot/newsbot_manager`（Django 管理远程发布节点）与本次无关，未参考。
- `trendforge` 的"智能采集"（多角度拆解、多语言 ReAct 检索、正反观点分析）是用户已有的成熟实现，本次**不涉及**，仅作为该能力未来演进方向的背景参考。

用户希望 Browserless 本身不再作为依赖（其开源协议条款近年有变化，具体条款需要在真正要用官方镜像时另行核实），改为完全自建的 Playwright 封装，因为 Playwright 本身是 Apache-2.0，没有协议顾虑。

## 2. 整体架构

两个独立容器，通过内部 docker network 通信，两者都不鉴权、不对公网暴露：

```
调用方（daily_stock_analysis 等）
        │  HTTP：异步 job API
        ▼
┌─────────────────────────┐   HTTP：脚本执行/URL 解析   ┌──────────────────────────┐
│   collector-service      │ ────────────────────────▶ │   playwright-service     │
│   (FastAPI)               │                            │   (FastAPI + Chromium)   │
│   - 对外异步 job API      │                            │   - Xvfb + Chromium      │
│   - 可插拔采集后端接口    │ ◀──────────────────────── │   - 反检测/stealth 配置  │
│   - Google News 后端      │   渲染后 HTML / 最终 URL    │     （搬自 newsbot）     │
│   - 内容解析 + SSRF 校验  │                            │   - 单容器多 session 并发│
│   - SQLite 去重缓存       │                            └──────────────────────────┘
└─────────────────────────┘
```

- **无鉴权**：两个服务都不做应用层鉴权，靠部署时的网络隔离（内部专用 docker network，端口不发布到宿主机公网接口）保证安全边界。如果未来 `collector-service` 需要暴露给可信网络之外的调用方，需要单独补充鉴权设计（本设计不覆盖）。
- **无状态（对外）**：`collector-service` 不做文章数据的正式持久化，不替调用方存储采集历史；调用方自己负责把结果存进自己的数据模型（如 `daily_stock_analysis` 的 `NewsIntel`）。
- **内部去重缓存（细节见第 4 节）**：`collector-service` 内部维护一个 SQLite 去重缓存，避免同一 URL 短期内被重复采集，这是内部效率优化，不是对外的数据存储能力，不违背"无状态"的定位。

## 3. `playwright-service`

- 单个长驻容器：Xvfb 虚拟屏 + Chromium（无需 VNC——newsgrab 不需要人工实时查看浏览器画面）。
- 启动配置与反检测配置直接复用 `newshub/newsbot/browser/browser_bot.py` + `newsbot/browser/entrypoint.sh` 验证过的配方：
  - Chrome 启动 flags：`--disable-blink-features=AutomationControlled` 等（具体全量 flag 列表在实现阶段从 `browser_bot.py`/`entrypoint.sh` 提取）。
  - `playwright_stealth` 库 + `context.add_init_script` 手动覆盖 `navigator.webdriver`/`plugins`/`languages`。
- 对外暴露一个"宽"API（对标 Browserless 的定位）：接受"导航到某 URL，等待 JS 跳转/渲染完成，返回最终 URL + 渲染后 HTML"这一类请求，在容器内部通过 Playwright 执行并返回结果。第一版只需要实现这一个具体动作（服务于 Google News 跳转链接解析），但 API 形状应保留"传入更通用的执行指令"的扩展空间，不要把接口写死成"只能解析 Google News"。
- 并发：单容器内部通过多个 browser context 处理并发请求，不做"每任务一个容器"的动态编排。

## 4. `collector-service`

### 4.1 对外 API（异步 job 模型）

- `POST /jobs`：提交一次采集请求（后端类型 + 查询参数），立即返回 `job_id`。
- `GET /jobs/{job_id}`：查询任务状态（`pending` / `running` / `done` / `failed`）与结果（完成时返回文章列表）。
- Job 状态存储在进程内存（不是正式数据库）——服务重启会丢失进行中的任务，调用方需要自己处理重试/超时；这与"无状态"定位一致，job 状态只是请求生命周期内的临时簿记，不是持久化的业务数据。

### 4.2 可插拔采集后端接口

- 现在就设计一个最小的 `Collector` 接口（大致形状：`collect(query: str, **params) -> List[Article]`），不做过度抽象（不预设跨后端的统一错误码体系、不预设通用参数模型），只保证"注册一个新后端"这件事有清晰入口。
- Google News 是第一个、也是本次唯一要实现的后端。

### 4.3 Google News 后端

- 参考（不照搬）`daily_stock_analysis` 分支里 `gnews_collector.py`/`content_parser.py`/`url_safety.py` 的思路，重写以适配新架构：
  - gnews 查询关键词、拿候选链接列表——不再需要对 gnews 库做 monkeypatch 避免它自己解析跳转链接，因为跳转解析这次统一交给 `playwright-service`。
  - 内容解析：三路 fallback（GNE / trafilatura / readability-lxml，与分支决策一致，不引入 newspaper4k）。
  - SSRF 校验：对 `playwright-service` 返回的最终 URL 做私有/内网地址校验，逻辑思路与分支里的 `url_safety.py` 一致，重新实现。

### 4.4 内部去重缓存

- **目的**：避免同一条新闻（同一个解析后真实 URL）在短时间窗口内被重复采集，节省 `playwright-service` 的浏览器自动化开销和内容解析开销——这是真实存在的场景，因为同一只股票的 Google News 查询窗口在多次调用之间高度重叠。
- **实现**：SQLite（不是 DuckDB——这是一个简单的键值查找场景，SQLite 是标准库自带、为事务型小查询设计的；DuckDB 面向分析型/列式查询，这里用不上它的优势）。
- **去重粒度**：按解析后的**真实 URL** 去重（不是原始 Google News 跳转链接，也不跟查询关键词联合）。
- **过期策略**：缓存条目 7 天后过期，过期后允许重新采集同一 URL。
- 命中缓存时，直接返回缓存的既有结果，不再调用 `playwright-service`。

## 5. 部署

- 项目根目录一个 `docker-compose.yml`，编排 `collector-service` + `playwright-service` + 内部专用 network。
- 两个服务技术栈统一为 Python + FastAPI，`collector-service` 额外依赖 SQLite（标准库自带，无需额外安装）。
- 项目定位为未来可能开源：代码、文档、示例都不能有 `stock_workspace`/`daily_stock_analysis` 专属假设；许可证选择、发布节奏留到实现阶段或更晚再定，本设计不覆盖。

## 6. 错误处理

- `playwright-service` 对任意请求失败（导航超时、页面崩溃等）返回明确的失败状态，不让异常穿透到 `collector-service`。
- `collector-service` 的 Google News 后端：单条链接解析/内容抽取失败时跳过该条，不影响其它候选链接的处理，任务整体仍可返回部分结果（除非全部候选链接都失败，此时任务状态为 `failed` 并带错误说明）。
- SSRF 校验失败的链接直接跳过，不解析、不缓存、不计入结果。

## 7. 测试

- `playwright-service`：对"导航+跳转等待+返回HTML"这个核心动作做集成测试（需要真实浏览器环境，可在 CI 里单独跑，不和 `collector-service` 的单元测试混在一起）。
- `collector-service`：
  - Google News 后端的查询/内容解析/SSRF 校验逻辑用 mock 覆盖单元测试（不依赖真实 `playwright-service`）。
  - 去重缓存的 TTL/命中/未命中逻辑单元测试。
  - Job API 的状态流转（`pending → running → done/failed`）单元测试。

## 8. 明确不做的事（本轮之外）

- 不做鉴权（依赖网络隔离；未来若需要对外暴露，需另行设计）。
- 不做除 Google News 外的第二个采集后端（本轮只把接口留出来，不实现第二个实现）。
- 不做角度拆解/多语言 ReAct 检索/正反观点分析（trendforge 风格的"智能采集"，用户已有成熟实现，非本项目范围）。
- 不做正式的文章数据库/历史查询能力（那是调用方自己的职责）。
- 不做开源许可证选择与发布流程设计（留到更明确的时间点）。
- 不做 `playwright-service` 的多容器/池化编排（单容器多 session 并发已经满足需求）。

## 9. 未决问题（留给实现计划阶段细化）

- `playwright-service` 的"宽 API"具体请求/响应 JSON 结构长什么样（字段命名、如何表达"要执行的动作"）。
- Job 状态的进程内存储具体用什么结构（简单 dict + 锁，还是引入 `asyncio.Queue`），以及 job 过期/清理策略。
- `browser_bot.py` 里除 stealth 配置外，哪些辅助逻辑（重试、`webhook`/`step_reporter` 回调）值得一并搬过来，哪些是 newsbot 特有、可以跳过。
