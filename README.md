# Travel Multi-Agent Planner

面向中国城市旅行规划的多 Agent 智能行程系统。项目将 `百炼大模型`、`腾讯位置服务`、规则化规划器、个性化运行时扩展和 React 前端串联起来，支持从自然语言偏好到可展示行程的完整流程。

当前版本重点增强了个性化定制能力：用户可以在备注里输入“第一天晚上吃火锅”“第二天晚上吃日料”“行程轻松一点”“第一天不要太早出发”等要求，系统会影响实际规划、餐饮候选排序、交通时间和前端展示，而不是只生成说明文字。

## 核心能力

- 出发地、目的地、天数、预算、兴趣、口味、酒店区域等基础规划输入
- 百炼大模型负责约束抽取、候选整理、攻略摘要和手册生成
- 腾讯位置服务负责城市确认、景点/酒店/餐饮检索、路线规划、天气和地图展示
- 多 Agent 生成每日景点、酒店、午餐、晚餐、交通分段和旅行手册
- SSE 流式接口实时推送规划进度，前端展示 Agent 执行过程
- 个性化备注可作用到餐饮、交通、行程节奏和候选补充
- 餐饮候选池支持近邻、沿途、区域和全局候选合并去重
- 未命中个性化餐饮时保留真实候选，并补充腾讯 API 附近匹配候选
- 过滤冰糖葫芦、奶茶、甜品、咖啡等不适合作午晚餐的候选
- 生成历史案例、Markdown/JSON 结果和腾讯地图播放器数据

## 项目结构

```text
travel_multi_agent_planner/
  agents/                    # 核心规划 Agent：景点、酒店、餐饮、交通、手册
  providers/                 # 百炼、腾讯地图、12306 等外部服务适配
  orchestrator.py            # 多 Agent 编排与个性化落地
  models.py                  # 行程、餐饮、酒店、证据等数据模型

personalization/
  engine.py                  # 个性化处理主流程
  agents/                    # 需求解析、代码生成、审查、验证、解释
  models/                    # 个性化流水线数据模型
  extensions/                # 运行时扩展包入口，生成文件默认不入库

backend/
  main.py                    # FastAPI 应用入口与个性化引擎初始化
  routers/
    plan.py                  # POST /api/plan/stream
    personalization.py       # 个性化需求处理、应用、清理

frontend/
  src/
    components/              # 首页、加载、结果、个性化面板、地图播放器
    api/                     # 后端 API client
    types/                   # 前端类型定义

tests/
  test_orchestrator.py       # 核心规划回归测试
  test_personalization_pipeline.py
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt

cd frontend
npm install
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

- `TENCENT_MAP_SERVER_KEY` 用于城市确认、POI 检索、餐饮候选、路线和天气
- `TENCENT_MAP_JS_KEY` 用于前端腾讯地图展示
- 没有关键在线数据时，系统倾向于显式报错，避免伪造景点、酒店或餐饮

### 3. 启动后端

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

### 4. 启动前端

```bash
cd frontend
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:5173/
```

## 推荐演示输入

基础规划：

```text
出发地：上海
目的地：南京
天数：4
预算：3000
兴趣：文化、美食、自然
口味：火锅、日料、面、烧烤
```

个性化备注：

```text
行程轻松一点。第一天晚上吃火锅，第二天晚上吃日料，第三天中午吃面，第四天晚上吃烧烤。第一天出发不要太早。
```

预期效果：

- 第一天下午/交通会避开过早出发
- 每天午晚餐候选池会扩大并去重
- 能匹配到的餐饮偏好会直接替换或优先安排
- 匹配不到时保留真实餐饮，同时给出附近个性化补充候选
- 晚餐不会安排冰糖葫芦、奶茶、甜品、咖啡等非正餐

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 后端健康检查 |
| `GET` | `/api/config` | 获取前端地图配置 |
| `POST` | `/api/plan/stream` | SSE 流式生成旅行规划 |
| `GET` | `/api/cases` | 获取历史案例 |
| `GET` | `/api/status/{case_id}` | 查询案例状态 |
| `POST` | `/api/personalize/process` | 解析并生成个性化修改方案 |
| `POST` | `/api/personalize/apply` | 应用个性化修改 |
| `POST` | `/api/personalize/clear` | 清理运行时个性化扩展 |

## 验证

后端与规划回归：

```bash
python -m unittest tests.test_orchestrator tests.test_personalization_pipeline -v
```

前端构建：

```bash
cd frontend
npm run build
```

## 实现说明

- 基础行程由 `TravelPlanningOrchestrator` 编排，多 Agent 逐步生成约束、景点、酒店、餐饮、交通、手册和验证结果
- 个性化模块会将自然语言需求拆分为子任务，生成受控运行时扩展，并经过审查、签名校验和导入验证
- 餐饮个性化优先使用真实候选；当前候选不满足时，会调用腾讯 API 扩展附近候选
- 餐饮费用按菜系、午晚餐、预算偏好、商圈和店型估算，避免零食级价格出现在正餐里
- `personalization/patches/` 和运行时生成的扩展文件属于本地运行状态，默认不提交到 Git

## 注意事项

- 当前主要面向中国城市和腾讯位置服务覆盖范围
- 真实在线规划耗时受腾讯检索、路线和天气接口影响，4 天游通常需要等待一段时间
- `.env`、本地日志、运行时缓存和生成的个性化补丁不应提交
- 旧版 `streamlit_app.py` 仍保留，但推荐使用 React + FastAPI 版本
