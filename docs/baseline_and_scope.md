# Baseline Research And Enhanced Scope

检查日期：`2026-04-11`

## 1. 研究结论

网上已经有不少旅行规划开源项目，但大多数只做到下面两类之一：

- 一类偏 `prompt + 搜索 + 输出 itinerary`
- 一类偏“产品界面做得好”，但 agent 分工和执行 trace 不够清晰

这意味着它们可以作为 baseline，但如果想稳定达到课程第三档，必须在此基础上继续做强：

- 多 agent 明确分工
- 更强的结构化中间结果
- 约束与预算校验
- 可追踪执行过程
- 更像“系统”而不是“一个生成页面”

## 2. 主要 baseline

### Baseline A: serpapi/travel-planning-agent

- [serpapi/travel-planning-agent](https://github.com/serpapi/travel-planning-agent)
- 借鉴点：工具接入明确，覆盖 flights / hotels / maps / web search，适合借鉴引用与 structured trace。
- 不足：更偏单 agent 工具编排，产品层展示不强。

### Baseline B: Electrolight123/AI-TravelPlanner-CrewAI

- [Electrolight123/AI-TravelPlanner-CrewAI](https://github.com/Electrolight123/AI-TravelPlanner-CrewAI)
- 借鉴点：角色分工清楚，适合作为 `Planner / Local Guide` 类型分工 baseline。
- 不足：偏命令行，工程规模较小。

### Baseline C: sourangshupal/Trip-Planner-using-CrewAI

- [sourangshupal/Trip-Planner-using-CrewAI](https://github.com/sourangshupal/Trip-Planner-using-CrewAI)
- 借鉴点：同时提供 CLI、FastAPI、Streamlit，更适合借鉴多入口演示。
- 不足：仍以 itinerary 生成为主，对预算与 explainability 支持不够强。

### Baseline D: RobertoCorti/gptravel

- [RobertoCorti/gptravel](https://github.com/RobertoCorti/gptravel)
- 借鉴点：前后端产品体验更成熟，适合借鉴交互和导出逻辑。
- 不足：更偏单助手式生成，多 agent 展示感不够强。

### Baseline E: embabel/tripper

- [embabel/tripper](https://github.com/embabel/tripper)
- 借鉴点：工程化最强，强调 domain model、deterministic planning 和工具整合。
- 不足：Java + Docker + MCP 偏重，不适合当前课程项目节奏。

## 3. 附加参考

- [OSU-NLP-Group/TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner)

它的价值在于提醒我们：最终项目不能只“看起来像”，还要考虑真实约束，例如预算、路线与常识约束。

## 4. 我们的组合式 baseline

- 借鉴 `serpapi/travel-planning-agent` 的工具与 trace 思路
- 借鉴 `AI-TravelPlanner-CrewAI` 和 `Trip-Planner-using-CrewAI` 的 agent 分工
- 借鉴 `gptravel` 的产品展示和导出思路
- 借鉴 `tripper` 的领域模型与工程化意识

## 5. 远超作业要求的增强目标

### 目标 1：真正的多 agent 分工

- `Planner Agent`
- `Transport Agent`
- `Budget Agent`
- `Food/Spot Agent`
- `Web Guide Agent`

### 目标 2：结构化中间结果

每个 agent 都输出结构化数据，而不是只输出一段长文本。

### 目标 3：执行 trace

保留 agent 输入摘要、输出摘要与关键决策，方便答辩展示。

### 目标 4：课程答辩可展示

- Streamlit 页面
- 每日行程
- 地图点位
- 预算表
- 导出旅行手册

### 目标 5：可升级到搜索增强版

第一阶段先用本地知识库保底，确保系统可以跑通。

第二阶段接入：

- Serper / Tavily / SerpApi
- OpenStreetMap / Nominatim / 高德
- OpenAI / DeepSeek / Qwen API

## 6. 当前版本任务拆解

### Phase 1

- 建立多 agent 数据结构
- 建立 orchestrator
- 完成样例知识库
- 完成预算、餐饮、景点、交通、导出逻辑
- 完成 Streamlit 页面

### Phase 2

- 接入在线搜索
- 接入真实交通与路线数据
- 增加酒店与天气模块
- 引入来源引用与证据展示

### Phase 3

- 增加 agent trace 时间线
- 增加更强的交互式地图
- 增加约束校验与自动修正

