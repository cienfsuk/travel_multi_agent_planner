# 旅行多智能体规划器 - Code Agent 自我重构系统

## 1. 目标定位

### 1.1 核心问题

**如何让 Code Agent 能够直接修改这个多 Agent 工程的代码，使其符合用户的独特需求？**

这不是一个"配置文件系统"的设计问题，而是一个**代码可理解性 + 可修改性**的工程问题。

### 1.2 预期效果

用户可以通过自然语言向 Code Agent 描述需求：

```
"我想要调整 PlannerAgent 的距离惩罚权重，让远距离景点的惩罚更小"
"我想要在 FoodSpotAgent 中添加一个新的评分因素：餐饮评分"
"我想要修改 ValidatorAgent 的校验阈值，让校验更严格"
"我想要添加一个新的 Agent 类型来处理行李寄存"
```

Code Agent 应该能够：
1. **理解代码结构**：知道 RequirementAgent 在哪个文件，负责什么
2. **定位修改点**：精确定位到需要修改的函数/类
3. **理解修改影响**：知道这个修改会影响哪些其他 Agent
4. **保持一致性**：修改后不破坏现有功能和 Agent 间协作

---

## 2. 当前问题分析

### 2.1 代码可理解性问题

| 问题 | 位置 | 影响 |
|------|------|------|
| 硬编码魔法数字 | `planner.py:240-247` | Agent 难以理解这些阈值的含义 |
| 复杂评分函数 | `food_spot.py` | 多因素综合评分难以拆解调整 |
| 缺乏类型标注 | 部分文件 | Code Agent 难以推断参数意图 |
| 跨 Agent 隐式依赖 | `orchestrator.py` | 修改一个 Agent 可能影响其他 Agent |

### 2.2 代码结构问题

```
当前问题：
- Agent 职责边界有时模糊（如 TravelNotesAgent 只是委托）
- 配置项分散在多个文件
- 评分权重分散在各个 Agent 内部
- 缺乏统一的"配置读取接口"
```

---

## 3. 解决方案：可修改性工程

### 3.1 核心理念

**让代码像一本书一样易读，像乐高一样易拆易装。**

| 维度 | 实现方式 |
|------|----------|
| 自文档化 | 详细的 docstring、类型标注、注释 |
| 模块化 | 每个 Agent 独立文件，清晰的输入输出 |
| 标准化 | 统一的配置读取、统一的日志接口 |
| 可追溯 | 关键决策点的注释说明 |

### 3.2 实施策略

#### 策略 A：自文档化（必须）

为每个 Agent、每个关键函数添加详细的 docstring：

```python
class PlannerAgent:
    """
    路线规划 Agent

    职责：
    - 根据 TripRequest 和候选 POI 生成每日行程
    - 支持 LLM 生成的初版路线或本地启发式算法
    - 校验失败时会调用 revise_daily_spot_plan 进行修正

    配置项（修改此处可调整规划行为）：
    - _bucket_cost(): 距离惩罚阈值（25/15/8km）
    - _cluster_day_buckets(): 每日景点数量限制
    - _order_bucket(): 景点排序逻辑

    Agent 间依赖：
    - 输入：SearchAgent 提供的 ranked_pois
    - 输出：HotelAgent, FoodSpotAgent, TransportAgent 的输入

    示例修改：
    - 调整远距离惩罚：修改 _bucket_cost() 中的 long_trip_penalty
    - 调整每日景点上限：修改 request.style 对应的 base_slots_per_day
    """

    def _bucket_cost(self, poi, bucket, capacity):
        """
        计算将 poi 加入 bucket 的代价

        代价组成：
        1. 距离代价：poi 到 bucket 中心的距离
        2. 跨区惩罚：poi.district 不在 bucket 已有区域时 +1.2
        3. 长途惩罚：距离>25km +14, >15km +6, >8km +2.5
        4. 容量惩罚：(实际数量 - 容量) * 0.8，超出越多惩罚越大

        如需调整惩罚力度，直接修改上述数值。
        """
        ...
```

#### 策略 B：配置集中化（推荐）

将所有"可调整参数"集中到一个文件：

**新文件**：`travel_multi_agent_planner/config/behavior_params.py`

```python
"""
行为参数配置 - 修改此文件可调整整个系统的规划行为

使用方式：
1. 找到对应的参数类别
2. 修改数值
3. 保存后重新运行即可生效

注意事项：
- 某些参数有物理意义（如距离单位为 km）
- 某些参数是相对权重（如评分系数）
- 修改后建议做一次完整的规划测试
"""

# ============================================================
# PlannerAgent - 路线规划参数
# ============================================================
PLANNER = {
    # 每日景点数量（按节奏）
    "slots_per_day": {"relaxed": 2, "balanced": 3, "dense": 4},

    # 距离惩罚阈值（km）和对应惩罚值
    "long_trip_penalty": {
        "thresholds_km": [25, 15, 8],  # 超过此距离开始惩罚
        "penalties": [14.0, 6.0, 2.5],  # 对应惩罚值
    },

    # 跨区惩罚系数
    "district_penalty": 1.2,

    # 超容量惩罚系数
    "fullness_penalty": 0.8,
}

# ============================================================
# FoodSpotAgent - 餐饮匹配参数
# ============================================================
FOOD_SPOT = {
    # 候选半径阶梯（km），从严格到宽松
    "distance_ladders": {
        "lunch": [1.0, 1.2, 1.8, 2.5],
        "dinner": [1.5, 1.8, 2.2, 2.5],
    },

    # 最小候选数阈值
    "min_candidates": {"lunch": 7, "dinner": 9},

    # 评分权重
    "score_weights": {
        "tag_match": 1.0,        # 兴趣标签匹配
        "taste_match": 1.2,       # 口味匹配
        "budget_fit": 2.0,       # 预算契合度
        "route_proximity": 1.5,   # 路线顺路程度
        "repeat_penalty": -3.0,   # 重复餐饮惩罚
    },

    # 费用估算系数
    "cost_factors": {
        "base_lunch": 42,
        "base_dinner": 58,
        "district_factor": {"premium": 1.16, "relaxed": 0.92, "default": 1.0},
        "venue_factor": {"小吃": 0.82, "火锅": 1.18, "咖啡": 0.96},
    },
}

# ============================================================
# ValidatorAgent - 校验参数
# ============================================================
VALIDATOR = {
    # 交通时长阈值（分钟）
    "transport_threshold_min": {"high": 210, "medium": 150},

    # 餐饮路线绕路阈值（km）
    "meal_detour_km": {"lunch": 1.0, "dinner": 1.5},

    # 评分扣分权重
    "score_weights": {"high": -18, "medium": -10, "low": -4},

    # 缓冲时间（分钟）
    "buffers": {
        "intercity_arrival": 75,
        "intercity_departure": 90,
        "rough_transfer": 12,
    },
}

# ============================================================
# LLM 调用参数
# ============================================================
LLM = {
    "temperature": 0.3,  # 越低越确定性，越高越有创意

    # Prompt 风格选项
    "style_hints": {
        "小红书风格": "活泼、有趣、注重拍照点",
        "预算友好": "性价比优先，减少门票开销",
        "城市漫游": "步行路线为主，深入街巷",
    },
}
```

#### 策略 C：CLAUDE.md 工程指南（必须）

**新文件**：`CLAUDE.md`（放在项目根目录）

```markdown
# Travel Multi-Agent Planner - Code Agent Guide

## 项目概述

这是一个多 Agent 协作的旅行规划系统，通过多个专业 Agent 的协作完成从用户请求到完整旅行方案的生成。

## Agent 架构

```
TravelPlanningOrchestrator (主编排器)
├── RequirementAgent      - 约束提取
├── SearchAgent           - 城市确认 + POI/餐饮/酒店检索
├── TravelNotesAgent      - 攻略摘要收集
├── PlannerAgent          - 路线生成 + 修正
├── HotelAgent            - 酒店匹配
├── FoodSpotAgent         - 餐饮匹配
├── TransportAgent        - 交通规划
├── BudgetAgent           - 预算计算
├── ConstraintValidatorAgent - 校验 + 评分
└── WebGuideAgent         - Markdown 手册生成
```

## 快速定位指南

### 修改每日景点数量限制
- 文件：`travel_multi_agent_planner/agents/planner.py`
- 函数：`_bucket_cost()`, `_heuristic_daily_spot_plan()`
- 参数：`base_slots_per_day = {"relaxed": 2, "balanced": 3, "dense": 4}`

### 修改距离惩罚权重
- 文件：`travel_multi_agent_planner/agents/planner.py`
- 函数：`_bucket_cost()`
- 变量：`long_trip_penalty`, `district_penalty`

### 修改餐饮评分因素
- 文件：`travel_multi_agent_planner/agents/food_spot.py`
- 函数：`_score_meal_candidate()`
- 参数：`tag_hits`, `taste_hits`, `budget_fit`, `route_distance_bonus`

### 修改校验阈值
- 文件：`travel_multi_agent_planner/agents/validator.py`
- 函数：`validate()`
- 参数：`transport_too_long`, `meal_detour_km`, `score_weights`

### 修改时间调度常量
- 文件：`travel_multi_agent_planner/scheduling.py`
- 常量：`DEFAULT_DAY_START_MINUTES`, `LUNCH_START_MINUTES`, 等

### 修改 LLM Prompt
- 文件：`travel_multi_agent_planner/providers/bailian.py`
- 函数：`draft_itinerary()`, `revise_itinerary()`
- 注意：Prompt 中已添加"不要写具体时间"的指令

### 修改地图路线逻辑
- 文件：`travel_multi_agent_planner/providers/map_provider.py`
- 函数：`_pick_transport_mode()`, `_request_best_route()`
- 路线质量检查：`_is_route_quality_ok()`

## 代码修改规范

### 1. 修改 Agent 参数
找到对应的参数位置后，直接修改数值。某些参数有物理单位（km、分钟），注意保持一致性。

### 2. 添加新 Agent
1. 在 `travel_multi_agent_planner/agents/` 创建新文件
2. 在 `orchestrator.py` 中导入并注册
3. 在 `create_plan()` 中添加调用逻辑
4. 在 `models.py` 中添加对应的数据模型

### 3. 修改 Agent 间协作
- 查看 `orchestrator.py` 的 `create_plan()` 方法了解调用顺序
- 查看每个 Agent 的输入输出类型（`models.py`）

### 4. 常见问题

**Q: 修改后没有效果？**
A: 检查是否保存了文件，确认修改的是正确路径。

**Q: 想要恢复默认？**
A: 从 Git 历史恢复：`git checkout HEAD -- <file>`

**Q: 如何测试修改？**
A: 重新规划一个行程，观察输出结果是否变化。

## 配置文件

所有可调整参数集中在：
- `travel_multi_agent_planner/config/behavior_params.py`

建议修改前阅读该文件的注释说明。
```

---

## 4. 实施计划

### 阶段一：代码自文档化

#### 任务 1.1：为 PlannerAgent 添加详细文档
- 每个函数的 docstring
- 参数说明
- 修改位置指引

#### 任务 1.2：为 FoodSpotAgent 添加详细文档
- 评分函数的权重说明
- 距离阶梯配置
- 费用估算系数说明

#### 任务 1.3：为 ValidatorAgent 添加详细文档

#### 任务 1.4：为 TransportAgent 添加详细文档
- 城市坐标映射表位置
- 距离费用估算公式

### 阶段二：配置集中化

#### 任务 2.1：创建 behavior_params.py
将所有可调整参数集中到一个文件。

#### 任务 2.2：修改各 Agent 读取配置
- PlannerAgent
- FoodSpotAgent
- ValidatorAgent
- TransportAgent

### 阶段三：CLAUDE.md + 文档完善

#### 任务 3.1：创建 CLAUDE.md
放置在项目根目录，Code Agent 读取此文件了解如何修改工程。

#### 任务 3.2：更新 README.md
添加"自定义修改指南"章节。

---

## 5. 文件变更清单

| 文件路径 | 操作 | 说明 |
|----------|------|------|
| `CLAUDE.md` | 新建 | Code Agent 指南 |
| `travel_multi_agent_planner/agents/planner.py` | 修改 | 添加详细 docstring |
| `travel_multi_agent_planner/agents/food_spot.py` | 修改 | 添加详细 docstring |
| `travel_multi_agent_planner/agents/validator.py` | 修改 | 添加详细 docstring |
| `travel_multi_agent_planner/agents/transport.py` | 修改 | 添加详细 docstring |
| `travel_multi_agent_planner/config/behavior_params.py` | 新建 | 集中配置参数 |
| `docs/customization_guide.md` | 新建 | 自定义修改指南 |
| `README.md` | 修改 | 添加自定义指南章节 |

---

## 6. 预期效果

### 6.1 Code Agent 使用场景

```
用户：我想让远距离景点的惩罚更小一些，这样景点可以安排得更分散

Code Agent 分析：
1. 理解需求："远距离惩罚"对应 PlannerAgent 的 _bucket_cost() 函数
2. 定位代码：planner.py 第 240-247 行
3. 修改参数：将 long_trip_penalty 的阈值从 [25, 15, 8] 改为 [35, 25, 15]
             或将惩罚值从 [14.0, 6.0, 2.5] 改为 [10.0, 4.0, 1.5]
4. 验证修改：修改正确，函数逻辑不变
```

### 6.2 自定义示例

| 需求 | 修改位置 | 预期行为 |
|------|----------|----------|
| 增加每日景点数 | planner.py slots_per_day | balanced 从 3 变为 4 |
| 放宽餐饮距离限制 | food_spot.py distance_ladders | lunch 从 [1.0, 1.2, 1.8, 2.5] 变为 [1.5, 2.0, 2.5, 3.0] |
| 更严格的校验 | validator.py transport_threshold_min | high 从 210 变为 180 |
| 增加 LLM 创意 | bailian.py temperature | 从 0.3 变为 0.5 |

---

## 7. 验证方式

### 7.1 自文档化验证
- 每个 Agent 的核心函数都有 docstring
- docstring 包含：职责、输入输出、关键参数、修改指引

### 7.2 配置集中化验证
- 所有可调整参数都在 behavior_params.py
- 修改后系统行为符合预期

### 7.3 CLAUDE.md 验证
- Code Agent 能够根据 CLAUDE.md 定位到正确的修改位置
- 修改后的代码功能正常
