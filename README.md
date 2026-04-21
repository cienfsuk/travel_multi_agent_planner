# Travel Multi-Agent Planner

一个面向课程第三档 `CodeAgent` 要求的智能旅行规划系统，当前版本重构为：

- `百炼大模型` 负责约束抽取、候选整理、攻略摘要和手册输出
- `腾讯位置服务` 负责城市确认、景点/酒店/餐饮检索、路线规划与中文地图展示
- `FastAPI` 后端提供 REST + SSE 流式 API
- `React + TypeScript` 前端提供现代化 Web 交互界面

当前版本的目标是：**只展示真实在线数据，不再使用系统合成或降级展示**。

## 项目架构

```
travel_multi_agent_planner/   # 核心 Agent 逻辑（百炼 + 腾讯地图）
backend/                      # FastAPI 后端
  main.py                     # 应用入口，CORS 配置
  routers/
    plan.py                   # POST /api/plan  （SSE 流式规划）
    cases.py                  # GET  /api/cases （历史案例列表）
    status.py                 # GET  /api/status（任务状态查询）
frontend/                     # React 前端（Vite + TypeScript + Tailwind）
  src/
    components/
      HomeView.tsx             # 出发地/目的地表单
      LoadingView.tsx          # 流式进度展示
      ResultView.tsx           # 行程结果展示
      SidebarView.tsx          # 历史案例侧边栏
      TripMapView.tsx          # 腾讯地图播放器嵌入
streamlit_app.py              # 旧版 Streamlit 入口（仍可用）
```

## 当前能力

- 输入出发地、目的地、预算、天数、兴趣、口味和酒店偏好
- 多 Agent 协作生成每日行程，流式实时推送进度
- 输出酒店、午餐、晚餐、景点和交通分段
- 生成 `plan.json / animation.json / player.html`
- 展示浏览器端腾讯 JS 地图播放器与路线说明表
- 展示真实来源证据和城市确认结果
- 导出 Markdown 和 JSON 版本旅行手册
- 历史案例侧边栏，可随时回溯已生成的行程

## 运行方式

### 1. 安装依赖

```bash
# Python 依赖
pip install -r requirements.txt

# 前端依赖
cd frontend && npm install
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，填入：

```env
DASHSCOPE_API_KEY=你的百炼 API Key
BAILIAN_MODEL=qwen-plus
TRAVEL_APP_MODE=online
TENCENT_MAP_SERVER_KEY=你的腾讯位置服务服务端 Key
TENCENT_MAP_JS_KEY=你的腾讯地图前端 JS Key
```

说明：

- `TENCENT_MAP_SERVER_KEY` 用于城市确认、POI 搜索和路线规划
- `TENCENT_MAP_JS_KEY` 用于腾讯地图浏览器端本地播放器
- 如果当前腾讯控制台给你的就是同一个 key，可以先把两个变量填成同一个值

### 3. 启动服务

**方式一：React 前端 + FastAPI 后端（推荐）**

终端 1 — 启动后端：

```bash
uvicorn backend.main:app --reload --port 8000
```

终端 2 — 启动前端开发服务器：

```bash
cd frontend && npm run dev
```

浏览器访问 `http://localhost:5173`

**方式二：旧版 Streamlit**

```bash
streamlit run streamlit_app.py
```

### 4. 推荐演示输入

- 出发地：`上海`
- 目的地：`南京`
- 天数：`3`
- 预算：`1500`
- 兴趣：`文化`、`美食`、`自然`

## API 说明

| 方法   | 路径                    | 说明                        |
| ------ | ----------------------- | --------------------------- |
| `GET`  | `/api/health`           | 服务健康检查                |
| `GET`  | `/api/config`           | 获取前端配置（JS Key 等）   |
| `POST` | `/api/plan`             | 发起规划，返回 SSE 流式事件 |
| `GET`  | `/api/cases`            | 获取历史案例列表            |
| `GET`  | `/api/status/{case_id}` | 查询指定案例状态            |

## 当前实现说明

- 不再生成系统合成城市、景点、酒店或餐饮
- 如果关键在线数据不足，系统会直接报错并停止生成
- 当前实现优先面向中国城市答辩场景
- 主播放器为浏览器端腾讯 JS 地图，`Pydeck` 和腾讯静态图作为备用视图
- 腾讯位置服务当前已接入：
  - 城市确认
  - POI / 酒店 / 餐饮检索
  - 步行 / 打车 / 公交地铁路径
  - `matrix / waypoint_order`
  - `placeDetail / reverseGeocoder / alongby / weather`
