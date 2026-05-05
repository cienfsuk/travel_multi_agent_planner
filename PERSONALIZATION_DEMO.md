# 个性化定制功能演示方案

## 一、演示定位

### 核心差异化卖点

| 原旅游Agent | 个性化定制模块 |
|------------|----------------|
| 自动生成行程 | **动态修改**已有行为 |
| 配置化参数 | **代码级扩展** |
| 单一场景 | **多意图复合** |
| 模板匹配 | **AI+模板混合生成** |

---

## 二、推荐演示案例

### 演示案例：南京3日游复杂个性化需求

**用户输入：**
```
我们去南京玩3天：
- 第一天中午想吃川菜、晚上吃火锅
- 第二天早上想吃日料、中午跳过不要餐
- 第三天行程要轻松一点、景点要分散一些、晚上想吃粤菜
另外我需要停车。
```

**系统自动拆分为8个独立需求：**

| 序号 | 需求内容 | 目标Agent | 生成模板 |
|------|----------|-----------|----------|
| 1 | 第一天中午想吃川菜 | food_spot | 川菜偏好 |
| 2 | 第一天晚上吃火锅 | food_spot | 火锅偏好 |
| 3 | 第二天早上想吃日料 | food_spot | 日本料理 |
| 4 | 第二天中午跳过不要餐 | food_spot | 跳过午餐 |
| 5 | 第三天行程要轻松一点 | planner | 轻松行程 |
| 6 | 第三天景点要分散一些 | planner | 分散景点 |
| 7 | 第三天晚上想吃粤菜 | food_spot | 粤菜偏好 |
| 8 | 我需要停车 | transport | 停车需求 |

---

## 三、技术亮点展示话术

### 亮点1：复合意图识别
> "用户说了一段话，但包含8个不同的需求。系统自动识别出这涉及食物偏好、行程安排、交通等多个维度，然后分别生成代码。"

### 亮点2：模板+AI混合生成
> "对于火锅、川菜这类明确需求，直接用模板生成（26+模板覆盖常见场景）。对于复杂场景，调用大模型生成定制代码。"

### 亮点3：运行时修改
> "生成的代码不是直接改源码，而是通过Monkey Patching在运行时修改Agent行为。这样可以随时撤销，不影响原始代码。"

### 亮点4：智能拆分
> "系统能识别'第一天中午想吃川菜、晚上吃火锅'这样的复合句，自动按餐次拆分。也能处理'第三天行程要轻松一点、景点要分散一些'这样的混合意图，自动按类型拆分。"

---

## 四、演示流程设计

### 4.1 准备阶段

```bash
# 终端1：启动后端
uvicorn backend.main:app --reload --port 8000

# 终端2：启动前端
cd frontend && npm run dev
```

### 4.2 演示步骤（建议15分钟）

#### Step 1: 正常规划（baseline）  ~3分钟
- 输入：上海→南京，3天，预算1500
- 展示：普通行程规划结果
- 目的：让观众看到原始Agent的能力

#### Step 2: 提出个性化需求  ~3分钟
- 点击"个性化定制"按钮
- 输入上述复杂需求文本
- 展示：系统如何解析并拆分需求（8个部分）

#### Step 3: 预览生成的代码  ~5分钟
- 展示生成的8个扩展文件
- 解释每个文件对应什么功能
- 重点展示1-2个关键代码片段

**关键代码示例 - 火锅偏好：**
```python
def _is_hotpot(food):
    name = food.name.lower()
    cuisine = food.cuisine.lower()
    return '火锅' in name or 'hotpot' in name

class CustomFoodSpotAgent(FoodSpotAgent):
    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        if food_options:
            hotpot = [f for f in food_options if _is_hotpot(f)]
            if hotpot:
                others = [f for f in food_options if not _is_hotpot(f)]
                food_options = hotpot + others
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
```

#### Step 4: 确认应用  ~2分钟
- 点击"应用修改"
- 展示：代码如何通过Monkey Patching生效

#### Step 5: 重新规划对比  ~2分钟
- 用同样的参数重新生成行程
- 展示：这次的结果如何体现了个性化需求

---

## 五、代码架构图

```
用户输入复杂需求
       ↓
┌─────────────────────────────────┐
│  PersonalizationEngine          │
│  ┌─────────────────────────────┐│
│  │ 1. 复合需求拆分              ││
│  │    _split_by_day()           ││  按"第X天"拆分
│  │    _split_by_meal()          ││  按"早上/中午/晚上"拆分
│  │    _split_by_intent()        ││  按"行程/食物"拆分
│  └─────────────────────────────┘│
│  ┌─────────────────────────────┐│
│  │ 2. 意图分类与模板匹配        ││
│  │    _detect_target()         ││  识别Agent类型
│  │    _post_process_code()      ││  选择对应模板
│  └─────────────────────────────┘│
│  ┌─────────────────────────────┐│
│  │ 3. 代码生成                  ││
│  │    模板生成（明确场景）       ││  26+模板覆盖
│  │    LLM生成（复杂场景）       ││  备用方案
│  └─────────────────────────────┘│
└─────────────────────────────────┘
       ↓
扩展文件 (personalization/extensions/)
       ↓
┌─────────────────────────────────┐
│  Monkey Patching                │
│  FoodSpotAgent  ───┐            │
│  PlannerAgent     ─┼──→ 定制行为 │
│  TransportAgent   ─┘            │
└─────────────────────────────────┘
```

---

## 六、模板覆盖场景

### FoodSpotAgent (餐饮)
| 场景 | 模板方法 |
|------|----------|
| 火锅 | `_generate_hotpot_extension` |
| 川菜 | `_generate_cuisine_extension(川菜)` |
| 日料/日本料理 | `_generate_cuisine_extension(日本)` |
| 粤菜 | `_generate_cuisine_extension(粤菜)` |
| 素食 | `_generate_vegetarian_extension` |
| 海鲜 | `_generate_seafood_extension` |
| 咖啡/甜品 | `_generate_cafe_extension` |
| 本地菜/老字号 | `_generate_local_cuisine_extension` |
| 跳过早餐/午餐/晚餐 | `_generate_skip_meal_extension` |

### PlannerAgent (行程)
| 场景 | 模板方法 |
|------|----------|
| 景点分散 | `_generate_spread_spots_extension` |
| 景点集中 | `_generate_cluster_spots_extension` |
| 轻松行程 | `_generate_relaxed_pacing_extension` |
| 紧凑行程 | `_generate_dense_schedule_extension` |
| 早上.focus | `_generate_morning_focus_extension` |
| 晚上.focus | `_generate_evening_focus_extension` |
| 避开拥挤 | `_generate_avoid_crowds_extension` |
| 拍照好看 | `_generate_photogenic_extension` |

### TransportAgent (交通)
| 场景 | 模板方法 |
|------|----------|
| 开车/自驾 | `_generate_car_mode_extension` |
| 需要停车 | `_generate_parking_extension` |
| 公共交通 | `_generate_public_transit_extension` |
| 步行 | `_generate_walking_extension` |
| 骑行 | `_generate_bike_extension` |

### BudgetAgent (预算)
| 场景 | 模板方法 |
|------|----------|
| 省钱 | `_generate_budget_extension(economical)` |
| 奢侈 | `_generate_budget_extension(premium)` |
| 平衡 | `_generate_budget_extension(balanced)` |

---

## 七、文件位置

| 功能 | 文件路径 |
|------|----------|
| 主编排引擎 | `personalization/engine.py` |
| 代码生成器 | `personalization/agents/code_modifier.py` |
| 需求拆分器 | `personalization/engine.py` (_split_compound_requirement) |
| API路由 | `backend/routers/personalization.py` |
| 前端组件 | `frontend/src/components/PersonalizationView.tsx` |
| 扩展存储 | `personalization/extensions/` |
| 演示文档 | `PERSONALIZATION_DEMO.md` |

---

## 八、演示注意事项

### 1. 强调的要点
- **实时性**：修改立即生效，不需要重启
- **可回滚**：每次修改都有快照，随时可撤销
- **可组合**：多个个性化需求可以叠加
- **代码级**：不是改配置，是改行为逻辑

### 2. 避免的问题
- 不要深入讲解代码实现细节
- 不要演示过于复杂的嵌套需求
- 不要展示CONFIG模式（已移除，仅保留CODE模式）

### 3. 预留QA

**Q: 为什么不用配置文件？**
> 配置文件只能改参数，我们的方案可以改行为逻辑。比如"想吃川菜"不是改一个数字，而是生成代码来过滤餐厅优先级。

**Q: 怎么保证生成的代码正确？**
> 有三层保障：1) 模板生成的代码经过验证；2) LLM生成有语法检查；3) 运行时验证机制。

**Q: 能处理多少种需求？**
> 26+种模板覆盖常见场景，复杂场景由AI生成。

**Q: 和原旅游Agent是什么关系？**
> 原Agent负责生成行程，个性化模块负责在运行时修改原Agent的行为。两者是正交的设计。