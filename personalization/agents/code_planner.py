"""Code Planning Agent - produces a structured modification IR before code generation."""

from __future__ import annotations

from typing import Any


class CodePlanningAgent:
    """Generate a stable IR that downstream code generation and repair can consume."""

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider

    def plan(self, task: dict[str, Any], resolution: dict[str, Any], parsed_parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        llm_plan = self._plan_with_llm(task, resolution, parsed_parameters or {})
        if llm_plan is not None:
            return llm_plan

        return self._rule_plan(task, resolution, source="rules")

    def _plan_with_llm(
        self,
        task: dict[str, Any],
        resolution: dict[str, Any],
        parsed_parameters: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self.llm or not hasattr(self.llm, "generate_json"):
            return None

        schema_hint = """
{
  "expected_behavior": "让首日节奏更轻松，并把晚餐偏好体现到结果里",
  "patch_style": "override_and_super",
  "acceptance_checks": ["override:create_daily_spot_plan", "syntax_valid", "runtime_patch_applied"]
}
""".strip()
        system_prompt = (
            "你是个性化代码规划 Agent。"
            "只输出结构化修改计划，不要输出 Python 代码。"
            "target_agent 和 target_method 必须与给定 resolution 保持一致。"
        )
        result = self.llm.generate_json(
            system_prompt,
            {"task": task, "resolution": resolution, "parsed_parameters": parsed_parameters},
            schema_hint=schema_hint,
            temperature=0.1,
        )
        if not isinstance(result, dict):
            return None

        plan = self._rule_plan(task, resolution, source="llm")
        expected_behavior = str(result.get("expected_behavior") or "").strip()
        acceptance_checks = [str(item).strip() for item in result.get("acceptance_checks", []) if str(item).strip()]
        patch_style = str(result.get("patch_style") or "").strip()
        if expected_behavior:
            plan["expected_behavior"] = expected_behavior
        if acceptance_checks:
            plan["acceptance_checks"] = acceptance_checks
        if patch_style:
            plan["patch_style"] = patch_style
        return plan

    def _rule_plan(self, task: dict[str, Any], resolution: dict[str, Any], source: str) -> dict[str, Any]:
        target_method = resolution["target_method"]
        checks = [
            f"override:{target_method}",
            "syntax_valid",
            "runtime_patch_applied",
        ]
        target_agent = resolution["target_agent"]
        if target_agent == "food_spot":
            checks.append("meal_preference_visible_in_result")
        elif target_agent == "transport":
            checks.append("late_departure_preference_visible_in_transport")
        elif target_agent == "planner":
            checks.append("pace_adjustment_visible_in_daily_plan")

        return {
            "target_agent": resolution["target_agent"],
            "target_method": resolution["target_method"],
            "change_strategy": resolution.get("change_strategy", "runtime_extension"),
            "expected_behavior": task["text"],
            "patch_style": "override_and_super",
            "acceptance_checks": checks,
            "scope": task.get("scope", {}),
            "source": source,
        }
