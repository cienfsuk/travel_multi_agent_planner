# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Travel Multi-Agent Planner 是一个多 Agent 协作的智能旅行规划系统，基于百炼大模型和腾讯位置服务生成真实在线数据的旅行方案。

```
travel_multi_agent_planner/   # 核心 Agent 逻辑（百炼 + 腾讯地图）
backend/                      # FastAPI 后端
frontend/                     # React 前端（Vite + TypeScript + Tailwind）
personalization/              # 个性化扩展模块
streamlit_app.py              # 旧版 Streamlit 入口
```

## 开发命令

### 启动服务（推荐）
终端 1 — 后端：
```bash
uvicorn backend.main:app --reload --port 8000
```

终端 2 — 前端：
```bash
cd frontend && npm run dev
```

### 测试
```bash
# 后端核心逻辑
python -m unittest tests.test_orchestrator -v

# 个性化管道
python -m unittest tests.test_personalization_pipeline -v

# 前端构建
cd frontend && npm run build
```

### 旧版 Streamlit
```bash
streamlit run streamlit_app.py
```

## 环境变量

配置在 `.env`（从 `.env.example` 复制）：
- `DASHSCOPE_API_KEY` - 阿里云百炼 API Key
- `BAILIAN_MODEL` - 模型名称（默认 qwen-plus）
- `TRAVEL_APP_MODE` - online 或 fallback
- `TENCENT_MAP_SERVER_KEY` - 腾讯位置服务端 Key
- `TENCENT_MAP_JS_KEY` - 腾讯地图前端 JS Key

## 架构

### Agent 层（travel_multi_agent_planner/agents/）

编排器入口：`orchestrator.py` → `create_plan()`

| Agent | 文件 | 职责 |
|-------|------|------|
| RequirementAgent | requirement.py | 约束提取 |
| SearchAgent | search.py | 城市确认 + POI/餐饮/酒店检索 |
| PlannerAgent | planner.py | 路线生成与修正 |
| FoodSpotAgent | food_spot.py | 餐饮匹配 |
| HotelAgent | hotel.py | 酒店匹配 |
| TransportAgent | transport.py | 交通规划 |
| BudgetAgent | budget.py | 预算计算 |
| ValidatorAgent | validator.py | 约束校验与评分 |
| WebGuideAgent | web_guide.py | Markdown 手册生成 |

### Provider 层（travel_multi_agent_planner/providers/）

| Provider | 职责 |
|----------|------|
| bailian.py | 百炼大模型调用 |
| search_provider.py | 腾讯 POI/酒店/餐饮检索 |
| map_provider.py | 腾讯路线规划与交通段构造 |
| intercity_provider.py | 城际铁路查询 |
| tencent_http.py | 腾讯 HTTP 请求封装 |

### 编排流程（`create_plan`）

```
TripRequest
  → RequirementAgent       约束提取
  → SearchAgent           城市确认 + POI/酒店/餐饮检索
  → PlannerAgent          路线生成与修正
  → TransportAgent        城际+城内交通
  → FoodSpotAgent          餐饮匹配
  → HotelAgent             酒店匹配
  → BudgetAgent            预算计算
  → ConstraintValidatorAgent 校验与评分
  → WebGuideAgent          Markdown 手册生成
  → TravelNotesAgent       旅行笔记
```

所有 Agent 通过 `on_trace` 回调实时推送 `AgentTraceStep`（SSE 流），前端 `LoadingView` 消费此事件流展示进度。

### 核心模块

- `models.py` - 数据模型定义
- `orchestrator.py` - 主编排入口
- `scheduling.py` - 时间轴与交通节点调度逻辑
- `persistence.py` - 案例保存/加载
- `config.py` - 配置读取

### 后端路由（backend/routers/）

| 路由 | 方法 | 路径 | 职责 |
|------|------|------|------|
| plan | POST | `/api/plan/stream` | SSE 流式规划 |
| cases | GET | `/api/cases` | 历史案例列表 |
| status | GET | `/api/status/{case_id}` | 查询案例状态 |
| route | POST | `/api/route/plan` | 路线规划 |
| health | GET | `/api/health` | 健康检查 |
| config | GET | `/api/config` | 前端配置 |
| **personalization** | POST | `/api/personalize/process` | 处理个性化需求 |
| **personalization** | POST | `/api/personalize/apply` | 应用确认的修改 |
| **personalization** | POST | `/api/personalize/rollback` | 回滚到快照 |
| **personalization** | GET | `/api/personalize/history` | 快照历史 |

### 前端组件（frontend/src/components/）

| 组件 | 职责 |
|------|------|
| HomeView.tsx | 出发地/目的地表单 |
| LoadingView.tsx | 流式进度展示 |
| ResultView.tsx | 行程结果展示 |
| SidebarView.tsx | 历史案例侧边栏 |
| TripMapView.tsx | 腾讯地图播放器 |
| **PersonalizationView.tsx** | **个性化定制弹窗（输入需求→查看修改→确认执行）** |

## 快速定位

| 修改目标 | 文件路径 |
|----------|----------|
| 距离惩罚权重 | `agents/planner.py` → `_bucket_cost()` |
| 每日景点数量 | `agents/planner.py` → `slots_per_day` |
| 餐饮候选半径 | `agents/food_spot.py` → `_distance_ladders` |
| 校验阈值 | `agents/validator.py` → `validate()` |
| 时间调度常量 | `scheduling.py` |
| LLM Prompt | `providers/bailian.py` |
| 地图路线逻辑 | `providers/map_provider.py` |

## 输出目录

生成成功后写入 `outputs/<case_id>/`：
- `plan.json` - 行程结构化数据
- `animation.json` - 地图动画数据
- `player.html` - 浏览器端地图播放器
- `latest_case.json` - 最新案例引用

## 推荐演示输入

- 出发地：`上海`
- 目的地：`南京`
- 天数：`3`
- 预算：`1500`
- 兴趣：`文化`、`美食`、`自然`

## 个性化扩展模块（personalization/）

独立的个性化定制引擎，允许用户通过自然语言需求修改系统行为。

### 设计原则

**CODE 模式**：所有个性化需求通过生成扩展文件 + Monkey Patching 实现运行时行为修改。

流程：
1. 复合需求智能拆分（按天、按餐次、按意图类型）
2. 每个子需求生成独立的扩展代码
3. 运行时通过 Monkey Patching 应用扩展
4. 支持快照与回滚

### Agent 体系

| Agent | 文件 | 职责 |
|-------|------|------|
| CodeModifierAgent | agents/code_modifier.py | 生成扩展补丁（模板+LLM混合） |

### 模板覆盖场景

- **FoodSpot**: 火锅、川菜、日料、粤菜、素食、海鲜、咖啡、跳过餐
- **Planner**: 分散景点、集中景点、轻松行程、紧凑行程
- **Transport**: 开车、停车、公共交通、骑行、步行
- **Budget**: 省钱、奢侈、平衡

### 关键文件

- `personalization/engine.py` - 主编排引擎，含复合需求拆分逻辑
- `personalization/models/personalization.py` - 数据模型
- `personalization/agents/code_modifier.py` - 模板系统（26+模板）
- `personalization/extensions/` - 扩展文件存储
- `backend/routers/personalization.py` - API 路由