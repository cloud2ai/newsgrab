# Per-Request Language/Region for collector-service

- 状态：已批准，待写实现计划
- 日期：2026-07-27
- 范围：仅 `collector-service` 的 `google_news` backend 支持按请求覆盖语言/地区；不改 `playwright-service`，不改 SSRF/去重/内容解析逻辑

## 1. 背景

`daily_stock_analysis` 正在设计一个"多地区宏观/政策深度洞察"能力（`MacroIntelAgent`），需要对同一个查询在 6 个固定地区（CN/JP/KR/SG/US/EU）分别检索 Google News。当前 `collector-service` 的 `GOOGLE_NEWS_LANGUAGE`/`GOOGLE_NEWS_REGION` 是**部署层全局配置**（`app/config.py`），一次部署只能服务一种语言/地区，无法满足"同一个部署、同一次分析、6 个地区各查一次"的需求。

## 2. 设计

`POST /jobs` 的 `params` 新增两个可选字段：`language`、`region`（字符串，直接对应 `gnews.GNews(language=..., country=...)` 的参数格式，如 `zh-CN`/`CN`、`ja`/`JP`）。

- 传了：本次 job 用请求里的 `language`/`region`，覆盖部署层的 `GOOGLE_NEWS_LANGUAGE`/`GOOGLE_NEWS_REGION`。
- 不传（或为空字符串）：保持现状，用部署层配置——**完全向后兼容**，现有调用方（daily_stock_analysis 的 `search_google_news` 工具）不用改。

## 3. 改动点

- `collector-service/app/schemas.py`：`JobRequest.params` 已经是自由 dict，不需要改 schema 校验层——语言/地区作为 `params` 里的普通可选键，在 `collectors/google_news.py::collect()` 里读取。
- `collector-service/app/collectors/google_news.py::collect(query, **params)`：新增读取 `params.get("language")`/`params.get("region")`，传给 `fetch_google_news_links` 的新可选参数。
- `collector-service/app/gnews_collector.py::fetch_google_news_links(keyword, max_results=10, days=7, language=None, region=None)`：新增两个可选参数，`GNews(...)` 构造时 `language=language or config.GOOGLE_NEWS_LANGUAGE, country=region or config.GOOGLE_NEWS_REGION`——不传时行为与现在完全一致。

## 4. 测试

- `gnews_collector.py` 的测试新增：传 `language`/`region` 时 `GNews` 构造用的是传入值而不是 `config` 默认值；不传时仍用 `config` 默认值（回归测试，确保向后兼容没破坏）。
- `google_news.py` 的编排测试新增：`collect(query, language="ja", region="JP")` 时，`fetch_google_news_links` 被调用时收到了这两个参数。
- 不改动任何现有测试的断言（这是纯新增可选参数，不改变任何默认行为）。

## 5. 明确不做的事

- 不做"语言/地区"的合法性校验（比如是否是真实存在的 gnews 语言代码）——上游调用方（daily_stock_analysis 的 `search_macro_news` 工具）负责传合法值，collector-service 只是透传，跟现有 `days`/`max_results` 参数的信任边界一致。
- 不改 `playwright-service`——跳转解析/渲染跟语言无关。
- 不改 SSRF 校验、去重缓存、内容解析器逻辑。
