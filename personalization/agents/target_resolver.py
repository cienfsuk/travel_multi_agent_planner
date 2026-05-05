"""Target Resolver Agent - resolves each atomic personalization task to a runtime agent/method."""

from __future__ import annotations

from typing import Any


METHOD_MAP = {
    "planner": "create_daily_spot_plan",
    "food_spot": "attach_meals",
    "transport": "build_day_transport",
    "hotel": "attach_hotels",
    "budget": "build_budget",
    "search": "rank_pois",
    "requirement": "parse",
    "validator": "validate",
}


class TargetResolverAgent:
    """Map atomic requirements to a known runtime agent and method."""

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider

    def resolve(self, task: dict[str, Any], parsed_parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        parsed_parameters = parsed_parameters or {}
        rule_result = self._resolve_with_rules(task, parsed_parameters)
        llm_result = self._resolve_with_llm(task, parsed_parameters)
        if llm_result is not None and self._looks_reasonable(llm_result, task):
            if llm_result["target_agent"] == rule_result["target_agent"]:
                return llm_result
        return rule_result

    def _resolve_with_rules(self, task: dict[str, Any], parsed_parameters: dict[str, Any]) -> dict[str, Any]:
        target_agent = self._detect_domain_from_task(task, parsed_parameters)
        return {
            "target_agent": target_agent,
            "target_method": METHOD_MAP[target_agent],
            "change_strategy": "runtime_extension",
            "capabilities": [target_agent],
            "source": "rules",
        }

    def _resolve_with_llm(self, task: dict[str, Any], parsed_parameters: dict[str, Any]) -> dict[str, Any] | None:
        if not self.llm or not hasattr(self.llm, "generate_json"):
            return None

        schema_hint = """
{
  "target_agent": "food_spot",
  "target_method": "attach_meals",
  "change_strategy": "runtime_extension",
  "capabilities": ["food", "meal"]
}
""".strip()
        system_prompt = (
            "You resolve one travel personalization sub-task to a fixed runtime agent. "
            "Choose only from planner, food_spot, transport, hotel, budget, search, requirement, validator. "
            "Meal and cuisine preferences -> food_spot. "
            "Departure-time, train, parking, transit, driving -> transport. "
            "Pacing and itinerary-density -> planner. "
            "Return JSON only."
        )
        result = self.llm.generate_json(
            system_prompt,
            {"task": task, "parsed_parameters": parsed_parameters},
            schema_hint=schema_hint,
            temperature=0.1,
        )
        if not isinstance(result, dict):
            return None
        target_agent = str(result.get("target_agent") or "").strip()
        target_method = str(result.get("target_method") or "").strip()
        if target_agent not in METHOD_MAP or not target_method:
            return None
        return {
            "target_agent": target_agent,
            "target_method": target_method,
            "change_strategy": str(result.get("change_strategy") or "runtime_extension"),
            "capabilities": [str(item).strip() for item in result.get("capabilities", []) if str(item).strip()],
            "source": "llm",
        }

    def _detect_domain_from_task(self, task: dict[str, Any], parsed_parameters: dict[str, Any]) -> str:
        text = str(task.get("text") or "")
        lowered = text.lower()
        meals = self._normalize_list(task.get("scope", {}).get("meals"))
        domains = self._normalize_list(parsed_parameters.get("domains"))

        if self._contains_any(lowered, ["酒店", "住宿", "民宿", "hotel", "stay", "accommodation"]):
            return "hotel"
        if self._contains_any(lowered, ["预算", "省钱", "便宜", "高端", "豪华", "budget", "cost", "price"]):
            return "budget"
        if self._contains_any(lowered, ["小众", "热门", "隐藏", "搜索", "排名", "search", "rank", "popular"]):
            return "search"
        if meals or self._contains_any(
            lowered,
            ["吃", "餐", "饭", "早餐", "早饭", "午饭", "午餐", "中午", "晚饭", "晚餐", "晚上", "夜宵", "火锅", "烧烤", "日料", "韩料", "面", "food", "meal", "restaurant"],
        ):
            return "food_spot"
        if self._contains_any(
            lowered,
            ["出发", "别太早", "不要太早", "太早", "晚点出发", "晚些出发", "交通", "高铁", "火车", "班次", "地铁", "公交", "打车", "停车", "自驾", "开车", "返程", "transport", "train", "drive", "metro", "bus"],
        ):
            return "transport"
        if self._contains_any(
            lowered,
            ["行程", "景点", "安排", "规划", "轻松", "紧凑", "节奏", "分散", "集中", "plan", "spot", "relaxed", "dense"],
        ):
            return "planner"
        if "transport" in domains:
            return "transport"
        if "food_spot" in domains:
            return "food_spot"
        if "planner" in domains:
            return "planner"
        return "planner"

    def _looks_reasonable(self, result: dict[str, Any], task: dict[str, Any]) -> bool:
        target_agent = str(result.get("target_agent") or "")
        text = str(task.get("text") or "").lower()
        meals = self._normalize_list(task.get("scope", {}).get("meals"))

        if meals and target_agent != "food_spot":
            return False
        if self._contains_any(text, ["火锅", "烧烤", "日料", "韩料", "早餐", "午饭", "午餐", "晚饭", "晚餐", "中午"]) and target_agent != "food_spot":
            return False
        if self._contains_any(text, ["出发", "高铁", "火车", "班次", "交通", "停车", "自驾", "开车"]) and target_agent not in {"transport", "planner"}:
            return False
        return True

    def _normalize_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _contains_any(self, text: str, keywords: list[str]) -> bool:
        return any(keyword.lower() in text for keyword in keywords)
