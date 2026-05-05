"""Code Generator Agent - builds controlled runtime extension patches from a structured IR."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..models import FilePatch, ModificationPatch, ParsedRequirement, PatchMetadata, PatchOperation
from .code_modifier import CodeModifierAgent


# Cuisine aliases for fuzzy keyword matching
_CUISINE_ALIASES = {
    "火锅": ["火锅", "涮锅", "鸳鸯锅", "川味", "麻辣", "hotpot", "huoguo"],
    "烧烤": ["烧烤", "烤肉", "bbq", "barbecue", "烤串", "韩式烤肉", "grill"],
    "川菜": ["川菜", "川味", "麻辣", "川香"],
    "粤菜": ["粤菜", "广式", "潮汕", "粤式"],
    "日料": ["日料", "日本料理", "寿司", "刺身", "和风"],
    "韩料": ["韩料", "韩国料理", "韩餐", "烤肉"],
    "海鲜": ["海鲜", "海产", "鱼虾", "贝类"],
    "素食": ["素食", "斋", "素菜", "植物性"],
    "小吃": ["小吃", "街边", "风味", "地方风味"],
}

_CUISINE_ALIAS_REVERSE: dict[str, list[str]] = {}
for canonical, aliases in _CUISINE_ALIASES.items():
    for alias in aliases:
        _CUISINE_ALIAS_REVERSE.setdefault(alias.lower(), []).append(canonical)

CUISINE_KEYWORD_MAP: dict[str, list[str]] = {k: v for k, v in _CUISINE_ALIASES.items()}


def _fuzzy_keyword_match(text: str, keyword: str) -> bool:
    """Check if keyword matches text, with cuisine alias expansion."""
    kw_lower = keyword.lower()
    if kw_lower in text.lower():
        return True
    # Check aliases
    for alias, canonicals in _CUISINE_ALIAS_REVERSE.items():
        if alias in kw_lower:
            return any(c in text.lower() for c in canonicals)
    # Check reverse: if text has a known cuisine, see if keyword maps to it
    for canonical, aliases in _CUISINE_ALIASES.items():
        if any(alias in text.lower() for alias in aliases):
            if kw_lower in [canonical.lower()] + [a.lower() for a in aliases]:
                return True
    return False


class CodeGeneratorAgent:
    """Generate runtime-safe extension patches from structured plans."""

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider
        self.modifier = CodeModifierAgent(llm_provider)

    def generate(
        self,
        requirement: ParsedRequirement,
        task: dict[str, Any],
        resolution: dict[str, Any],
        code_plan: dict[str, Any],
        base_path: Path,
    ) -> tuple[ModificationPatch, str]:
        target_agent = str(code_plan.get("target_agent") or resolution.get("target_agent") or "").strip()
        if target_agent in self.AGENT_CLASS_MAP:
            code = self._build_runtime_template(requirement, task, resolution, code_plan)
            patch = self._build_patch(requirement, task, resolution, code_plan, code, "template")
            return patch, "template"

        # Try LLM generation first if available
        if self.llm and hasattr(self.llm, "generate_code"):
            try:
                llm_code = self._generate_code_candidate(requirement, task, resolution, code_plan)
                if llm_code:
                    validated = self.modifier._fix_imports(llm_code)
                    if self._is_valid_extension(validated, resolution):
                        patch = self._build_patch(requirement, task, resolution, code_plan, validated, "llm")
                        return patch, "llm"
            except Exception:
                pass
        # Fall back to template-based generation
        code = self._build_runtime_template(requirement, task, resolution, code_plan)
        patch = self._build_patch(requirement, task, resolution, code_plan, code, "template")
        return patch, "template"

    def repair(
        self,
        requirement: ParsedRequirement,
        task: dict[str, Any],
        resolution: dict[str, Any],
        code_plan: dict[str, Any],
        current_patch: ModificationPatch,
        errors: list[str],
    ) -> tuple[ModificationPatch | None, str]:
        try:
            repaired_code = self._generate_repair_candidate(
                requirement=requirement,
                task=task,
                resolution=resolution,
                code_plan=code_plan,
                current_patch=current_patch,
                errors=errors,
            )
        except Exception:
            repaired_code = ""
        if not repaired_code:
            return None, ""
        patch = self._build_patch(requirement, task, resolution, code_plan, repaired_code, "repaired")
        return patch, "repaired"

    METHOD_SIGNATURES = {
        "food_spot.attach_meals": (
            "def attach_meals(self, request: TripRequest, daily_spot_plans: list[dict], "
            "food_options: list[FoodVenue] | None = None, used_food_keys: set[str] | None = None) -> list[dict]:"
        ),
        "planner.create_daily_spot_plan": (
            "def create_daily_spot_plan(self, request: TripRequest, ranked_pois: list[PointOfInterest], "
            "constraints: TravelConstraints, llm_provider=None, policy=None) -> tuple[list[dict], str]:"
        ),
        "transport.build_day_transport": (
            "def build_day_transport(self, request: TripRequest, profile: CityProfile, "
            "day_spots: list[PointOfInterest], hotel: HotelVenue | None, segments: list[TransportSegment], "
            "day: int, total_days: int) -> DailyTransportPlan:"
        ),
        "hotel.attach_hotels": (
            "def attach_hotels(self, request: TripRequest, daily_spot_plans: list[dict], "
            "hotel_options: list[HotelVenue], llm_provider=None, policy: HotelScoringPolicy | None = None) -> tuple[list[dict], list[str]]:"
        ),
        "budget.build_budget": (
            "def build_budget(self, request: TripRequest, profile: CityProfile, day_plans: list[DayPlan], "
            "round_trip_transport_cost: float, round_trip_note: str) -> BudgetSummary:"
        ),
        "search.rank_pois": (
            "def rank_pois(self, pois: list[PointOfInterest], request: TripRequest, "
            "constraints: TravelConstraints) -> list[PointOfInterest]:"
        ),
    }
    AGENT_CLASS_MAP = {
        "food_spot": "FoodSpotAgent",
        "planner": "PlannerAgent",
        "transport": "TransportAgent",
        "hotel": "HotelAgent",
        "budget": "BudgetAgent",
        "search": "SearchAgent",
    }

    def _generate_code_candidate(
        self,
        requirement: ParsedRequirement,
        task: dict[str, Any],
        resolution: dict[str, Any],
        code_plan: dict[str, Any],
    ) -> str:
        if not self.llm or not hasattr(self.llm, "generate_code"):
            return ""
        target_agent = str(code_plan.get("target_agent") or resolution.get("target_agent") or "").strip()
        target_method = str(code_plan.get("target_method") or resolution.get("target_method") or "").strip()
        target_sig = f"{target_agent}.{target_method}"
        sig = self.METHOD_SIGNATURES.get(target_sig, f"def {target_method}(self, ...):")
        class_name = self.AGENT_CLASS_MAP.get(target_agent, f"{target_agent.title()}Agent")
        super_call = self._super_call_example(target_agent, target_method)
        system_prompt = (
            "You generate a single Python runtime extension file for a travel personalization system. "
            "Return ONLY the complete Python code, no markdown fences, no explanations.\n\n"
            "IMPORTANT rules:\n"
            "1. The custom agent class must have EXACTLY this method signature:\n"
            f"   {sig}\n"
            "2. Use built-in list/dict/set syntax only. Do not use typing aliases like List or Dict.\n"
            f"3. Inside the method, call {super_call}\n"
            "4. Apply your personalization logic after calling super()\n"
            "5. At the bottom, apply the monkey patch:\n"
            f"   import travel_multi_agent_planner.agents.{target_agent} as m\n"
            f"   m.{class_name} = Custom{class_name}\n"
            f"6. The class name must be Custom{class_name}\n"
        )
        user_payload = {
            "requirement": requirement.raw_text,
            "target_signature": target_sig,
            "target_agent": target_agent,
            "target_method": target_method,
            "task": task,
            "change_strategy": code_plan.get("change_strategy", "runtime_extension"),
            "expected_signature": sig,
            "class_name": class_name,
            "super_call": super_call,
        }
        code = self.llm.generate_code(system_prompt=system_prompt, user_payload=user_payload, temperature=0.2)
        if not code:
            return ""
        # Strip markdown fences if any
        cleaned = code.strip()
        cleaned = re.sub(r"^```python\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
        if not cleaned:
            return ""
        return cleaned

    def _generate_repair_candidate(
        self,
        requirement: ParsedRequirement,
        task: dict[str, Any],
        resolution: dict[str, Any],
        code_plan: dict[str, Any],
        current_patch: ModificationPatch,
        errors: list[str],
    ) -> str:
        if not self.llm or not hasattr(self.llm, "generate_repair"):
            return ""
        existing_code = current_patch.patches[0].new_snippet if current_patch.patches else ""
        system_prompt = (
            "You repair a single Python runtime extension file for a travel personalization system. "
            "Return only the full corrected Python code."
        )
        payload = {
            "requirement": requirement.raw_text,
            "task": task,
            "resolution": resolution,
            "code_plan": code_plan,
            "existing_code": existing_code,
            "errors": errors,
            "target_signature": self._target_signature(resolution),
        }
        code = self.llm.generate_repair(system_prompt, payload, temperature=0.1)
        return self._sanitize_generated_code(code, resolution)

    def _sanitize_generated_code(self, code: str | None, resolution: dict[str, Any]) -> str:
        if not code:
            return ""
        cleaned = code.strip()
        cleaned = re.sub(r"```python\s*", "", cleaned)
        cleaned = re.sub(r"```\s*", "", cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            return ""
        if self._is_valid_extension(cleaned, resolution):
            return self.modifier._fix_imports(cleaned)
        return ""

    def _build_runtime_template(
        self,
        requirement: ParsedRequirement,
        task: dict[str, Any],
        resolution: dict[str, Any],
        code_plan: dict[str, Any],
    ) -> str:
        target_agent = str(code_plan.get("target_agent") or resolution.get("target_agent") or "").strip()
        task_items = self._task_items(task, code_plan)
        if target_agent == "planner":
            return self._build_planner_extension(requirement, task_items)
        if target_agent == "transport":
            return self._build_transport_extension(requirement, task_items)
        if target_agent == "food_spot":
            return self._build_food_extension(requirement, task_items)
        if target_agent == "hotel":
            return self.modifier._build_hotel_runtime_extension(requirement.raw_text)
        if target_agent == "search":
            return self.modifier._build_search_runtime_extension(requirement.raw_text)
        if target_agent == "budget":
            return self.modifier._build_budget_runtime_extension(requirement.raw_text)
        return self.modifier._build_planner_runtime_extension(requirement.raw_text)

    def _build_planner_extension(self, requirement: ParsedRequirement, task_items: list[dict[str, Any]]) -> str:
        rules: list[dict[str, Any]] = []
        relax_style = False
        for item in task_items:
            text = str(item.get("text") or "").strip()
            lowered = text.lower()
            day = self._extract_day_number(item)
            kind = "general"
            if any(token in text for token in ["别太早", "不要太早", "不宜过早", "晚点出发", "晚些出发"]) or "late start" in lowered:
                kind = "late_start"
                relax_style = True
            elif any(token in text for token in ["轻松", "放松", "别太赶", "不要太赶", "节奏慢一些"]) or "relaxed" in lowered:
                kind = "relaxed_pacing"
                relax_style = True
            rules.append({"day": day, "label": text, "kind": kind})

        rules_literal = repr(rules)
        summary_json = json.dumps("；".join(item["label"] for item in rules if item.get("label")), ensure_ascii=False)
        return f'''from __future__ import annotations
import copy
from travel_multi_agent_planner.agents.planner import PlannerAgent

RULES = {rules_literal}
APPLY_RELAXED_STYLE = {relax_style!r}
SUMMARY_TEXT = {summary_json}

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement.raw_text[:80]}"""

    def create_daily_spot_plan(self, request, ranked_pois, constraints, llm_provider=None, policy=None):
        runtime_request = request
        if APPLY_RELAXED_STYLE and getattr(request, "style", "") != "relaxed":
            runtime_request = copy.copy(request)
            runtime_request.style = "relaxed"
        daily_plans, planning_note = super().create_daily_spot_plan(
            runtime_request, ranked_pois, constraints, llm_provider, policy
        )
        for plan in daily_plans:
            day = int(plan.get("day", 0))
            notes = list(plan.get("notes", []))
            trimmed_for_day = False
            for rule in RULES:
                if rule.get("day") not in {{None, day}}:
                    continue
                if rule.get("kind") == "late_start" and len(plan.get("spots", [])) > 1 and not trimmed_for_day:
                    plan["spots"] = list(plan.get("spots", []))[:-1]
                    trimmed_for_day = True
                notes.append("【个性化】" + str(rule.get("label", "")))
            plan["notes"] = notes
            if plan.get("spots"):
                plan["theme"] = self._build_theme(plan["spots"])
        if SUMMARY_TEXT:
            planning_note = (planning_note + "；" + SUMMARY_TEXT) if planning_note else SUMMARY_TEXT
        return daily_plans, planning_note

import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _build_transport_extension(self, requirement: ParsedRequirement, task_items: list[dict[str, Any]]) -> str:
        min_departure_minutes = 9 * 60
        labels: list[str] = []
        for item in task_items:
            text = str(item.get("text") or "").strip()
            lowered = text.lower()
            if text:
                labels.append(text)
            if any(token in text for token in ["睡到自然醒", "十点后", "10点后", "十点以后", "10点以后"]) or "10:00" in lowered:
                min_departure_minutes = max(min_departure_minutes, 10 * 60)
            elif any(token in text for token in ["不宜过早", "不要太早", "别太早", "晚点出发", "晚些出发", "不要赶早车", "不要赶车太早"]):
                min_departure_minutes = max(min_departure_minutes, 9 * 60)
        summary_text = "；".join(label for label in labels if label)
        return f'''from __future__ import annotations
import travel_multi_agent_planner.agents.transport as transport_module
from travel_multi_agent_planner.agents.transport import TransportAgent

MIN_DEPARTURE_MINUTES = {min_departure_minutes}
SUMMARY_TEXT = {json.dumps(summary_text, ensure_ascii=False)}

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement.raw_text[:80]}"""

    def _format_minutes(self, minutes):
        hour = max(0, int(minutes) // 60) % 24
        minute = max(0, int(minutes) % 60)
        return f"{{hour:02d}}:{{minute:02d}}"

    def _resolve_intercity_option(self, origin_city, destination_city, travel_date):
        origin_name = self._normalize_city_name(origin_city)
        destination_name = self._normalize_city_name(destination_city)
        cache_key = (origin_name, destination_name, travel_date)
        if cache_key in self._intercity_choice_cache:
            return self._intercity_choice_cache[cache_key]
        provider = self.intercity_provider
        if provider is None or not hasattr(provider, "query_options"):
            self._intercity_choice_cache[cache_key] = None
            return None
        try:
            options = provider.query_options(origin_name, destination_name, travel_date, limit=20)
        except Exception:
            options = []
        filtered = []
        for option in options:
            minutes = self._time_to_minutes(option.depart_time)
            if minutes is None or minutes >= MIN_DEPARTURE_MINUTES:
                filtered.append(option)
        choice = self._select_option(filtered or options)
        self._intercity_choice_cache[cache_key] = choice
        return choice

    def _departure_penalty(self, depart_time):
        base_penalty = super()._departure_penalty(depart_time)
        minutes = self._time_to_minutes(depart_time)
        if minutes is None:
            return base_penalty
        if minutes < MIN_DEPARTURE_MINUTES:
            return base_penalty + (MIN_DEPARTURE_MINUTES - minutes) * 4 + 180
        return base_penalty

    def infer_intercity_segment(self, request, profile, from_label, to_label, leg="outbound"):
        segment = super().infer_intercity_segment(request, profile, from_label, to_label, leg=leg)
        depart_minutes = self._time_to_minutes(segment.depart_time)
        if depart_minutes is None or depart_minutes >= MIN_DEPARTURE_MINUTES:
            return segment
        adjusted_depart = MIN_DEPARTURE_MINUTES
        adjusted_arrive = adjusted_depart + max(60, int(segment.duration_minutes or 90))
        original_depart = segment.depart_time or "待定"
        original_arrive = segment.arrive_time or "待定"
        segment.depart_time = self._format_minutes(adjusted_depart)
        segment.arrive_time = self._format_minutes(adjusted_arrive)
        segment.confidence = "personalized"
        segment.description = (
            "【个性化】已避开过早车次，建议改乘 "
            + segment.depart_time
            + " 以后出发的班次。原始查询参考为 "
            + original_depart
            + " -> "
            + original_arrive
            + "。"
        )
        return segment

    def build_day_transport(self, request, profile, day_spots, hotel, segments, day, total_days):
        result = super().build_day_transport(request, profile, day_spots, hotel, segments, day, total_days)
        if SUMMARY_TEXT:
            result.intra_city = (result.intra_city + " " + "【个性化】" + SUMMARY_TEXT).strip()
            if result.route_summary:
                result.route_summary = result.route_summary + " | " + "【个性化】" + SUMMARY_TEXT
            else:
                result.route_summary = "【个性化】" + SUMMARY_TEXT
        return result

transport_module.TransportAgent = CustomTransportAgent
'''

    def _build_food_extension(self, requirement: ParsedRequirement, task_items: list[dict[str, Any]]) -> str:
        rules = self._build_food_rules(task_items)
        if not rules:
            return self.modifier._generate_food_preference_extension(requirement.raw_text)
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent
from travel_multi_agent_planner.models import MealRecommendation

RULES = {repr(rules)}

# Cuisine aliases for fuzzy keyword matching
_CUISINE_ALIASES = {{
    "火锅": ["火锅", "涮锅", "鸳鸯锅", "川味", "麻辣", "hotpot", "huoguo"],
    "烧烤": ["烧烤", "烤肉", "bbq", "barbecue", "烤串", "韩式烤肉", "grill"],
    "川菜": ["川菜", "川味", "麻辣", "川香"],
    "粤菜": ["粤菜", "广式", "潮汕", "粤式"],
    "日料": ["日料", "日本料理", "寿司", "刺身", "和风"],
    "韩料": ["韩料", "韩国料理", "韩餐", "烤肉"],
    "海鲜": ["海鲜", "海产", "鱼虾", "贝类"],
    "素食": ["素食", "斋", "素菜", "植物性"],
    "小吃": ["小吃", "街边", "风味", "地方风味"],
}}
_CUISINE_ALIAS_REVERSE: dict[str, list[str]] = {{}}
for canonical, aliases in _CUISINE_ALIASES.items():
    for alias in aliases:
        _CUISINE_ALIAS_REVERSE.setdefault(alias.lower(), []).append(canonical)

def _fuzzy_keyword_match(text: str, keyword: str) -> bool:
    """Check if keyword matches text, with cuisine alias expansion."""
    kw_lower = keyword.lower()
    text_lower = text.lower()
    if kw_lower in text_lower:
        return True
    for alias, canonicals in _CUISINE_ALIAS_REVERSE.items():
        if alias in kw_lower:
            return any(c in text_lower for c in canonicals)
    for canonical, aliases in _CUISINE_ALIASES.items():
        if any(a in text_lower for a in aliases):
            if kw_lower in [canonical.lower()] + [a.lower() for a in aliases]:
                return True
    return False

def _food_text(food):
    parts = [
        getattr(food, "name", ""),
        getattr(food, "cuisine", ""),
        getattr(food, "description", ""),
    ]
    return " ".join(str(part) for part in parts if part).lower()

def _matches_preference(food, keywords):
    text = _food_text(food)
    return any(_fuzzy_keyword_match(text, str(keyword)) for keyword in keywords)

def _matches_meal(meal, keywords):
    text = " ".join([
        getattr(meal, "venue_name", ""),
        getattr(meal, "cuisine", ""),
    ]).lower()
    return any(_fuzzy_keyword_match(text, str(keyword)) for keyword in keywords)

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement.raw_text[:80]}"""

    INTEREST_LABELS = {{
        "culture": "文化",
        "food": "美食",
        "nature": "自然",
        "history": "历史",
        "photography": "摄影",
        "shopping": "购物",
        "night": "夜游",
        "relaxed": "慢节奏",
        "tea": "茶文化",
    }}

    TASTE_LABELS = {{
        "spicy": "辣",
        "sweet": "甜",
        "savory": "咸香",
        "sour": "酸",
        "fresh": "鲜",
        "light": "清淡",
        "bbq": "烧烤",
        "barbecue": "烧烤",
        "hotpot": "火锅",
    }}

    def _clone_runtime_plan(self, plan):
        cloned = dict(plan)
        cloned["notes"] = list(plan.get("notes", []))
        cloned["meal_candidate_pools"] = {{
            name: list(items or [])
            for name, items in (plan.get("meal_candidate_pools") or {{}}).items()
        }}
        cloned["meal_paths"] = {{
            name: list(items or [])
            for name, items in (plan.get("meal_paths") or {{}}).items()
        }}
        return cloned

    def _format_preference_labels(self, values, mapping, fallback):
        labels = []
        for raw in list(values or [])[:2]:
            text = str(raw or "").strip()
            if not text or set(text) == {{"?"}}:
                continue
            label = mapping.get(text.lower(), mapping.get(text, text))
            if label not in labels:
                labels.append(label)
        return "、".join(labels) if labels else fallback

    def _build_reason(self, food, meal_type, interests, tastes):
        interest_hint = self._format_preference_labels(interests, self.INTEREST_LABELS, "城市体验")
        taste_hint = self._format_preference_labels(tastes, self.TASTE_LABELS, "本地风味")
        meal_label = "午餐" if meal_type == "lunch" else "晚餐"
        return f"{{meal_label}}安排在 {{food.name}}，兼顾 {{interest_hint}} 与 {{food.cuisine}}，口味更贴近 {{taste_hint}}。"

    def _apply_candidate_priorities(self, plan):
        day = int(plan.get("day", 0))
        pools = plan.get("meal_candidate_pools") or {{}}
        notes = plan.setdefault("notes", [])
        for rule in RULES:
            if rule.get("day") not in {{None, day}}:
                continue
            meal_type = rule.get("meal")
            if meal_type not in {{"lunch", "dinner"}}:
                continue
            candidates = list(pools.get(meal_type) or [])
            if not candidates:
                continue
            preferred = [food for food in candidates if _matches_preference(food, rule.get("keywords", []))]
            if preferred:
                others = [food for food in candidates if food not in preferred]
                pools[meal_type] = preferred + others
                notes.append("【个性化】" + str(rule.get("label", "")) + "，已优先排序相关候选。")
        plan["meal_candidate_pools"] = pools

    def _build_personalized_meal(self, meal_type, food, request, plan, rule):
        day_index = int(plan.get("day", 1))
        meal_paths = plan.get("meal_paths") or {{}}
        route_path = meal_paths.get(meal_type, [])
        anchor = self._meal_anchor(plan.get("spots", []), meal_type)
        route_distance = self._route_distance(food, route_path)
        anchor_distance = self._anchor_distance(food, anchor)
        reason = self._build_reason(food, meal_type, request.interests, request.food_tastes)
        reason = reason + " 已按“" + str(rule.get("label", "")) + "”偏好优先匹配。"
        return MealRecommendation(
            venue_name=food.name,
            meal_type=meal_type,
            estimated_cost=self._cost_for_meal(food, meal_type, day_index),
            reason=reason,
            venue_district=food.district,
            cuisine=food.cuisine,
            lat=food.lat,
            lon=food.lon,
            anchor_distance_km=round(anchor_distance, 2),
            route_distance_km=round(route_distance, 2),
            fallback_used=False,
            selection_tier="personalized",
            source_evidence=food.source_evidence[:2],
        )

    def _build_placeholder_meal(self, meal_type, plan, rule):
        label = str(rule.get("label", "个性化餐饮偏好"))
        return MealRecommendation(
            venue_name=label + "（待到店确认）",
            meal_type=meal_type,
            estimated_cost=88.0 if meal_type == "dinner" else 48.0,
            reason="未检索到明显匹配候选，已为该餐次保留“" + label + "”占位，请出发前二次确认。",
            venue_district=str(plan.get("spots", [])[0].district if plan.get("spots") else ""),
            cuisine=label,
            lat=0.0,
            lon=0.0,
            anchor_distance_km=0.0,
            route_distance_km=0.0,
            fallback_used=True,
            selection_tier="personalized-placeholder",
            source_evidence=[],
        )

    def _format_supplemental_food_candidates(self, candidates, current_meal, keywords, limit=3):
        current_name = str(getattr(current_meal, "venue_name", "") or "").strip()
        labels = []
        seen = set()
        for food in list(candidates or []):
            name = str(getattr(food, "name", "") or "").strip()
            if not name or name == current_name or name in seen:
                continue
            if not _matches_preference(food, keywords):
                continue
            evidence = list(getattr(food, "source_evidence", []) or [])
            if not evidence:
                continue
            seen.add(name)
            cuisine = str(getattr(food, "cuisine", "") or "餐饮").strip()
            district = str(getattr(food, "district", "") or "").strip()
            provider = str(getattr(evidence[0], "provider_label", "") or getattr(evidence[0], "provider", "") or "腾讯位置服务")
            meta = " / ".join(part for part in [cuisine, district, provider] if part)
            labels.append(name + "（" + meta + "）" if meta else name)
            if len(labels) >= limit:
                break
        return "；".join(labels)

    def _enforce_rule_on_plan(self, request, plan, rule, used_food_keys, fallback_foods):
        day = int(plan.get("day", 0))
        if rule.get("day") not in {{None, day}}:
            return
        meal_type = rule.get("meal")
        if meal_type not in {{"lunch", "dinner"}}:
            return
        meals = list(plan.get("meals") or [])
        target_index = None
        for index, meal in enumerate(meals):
            if getattr(meal, "meal_type", "") == meal_type:
                target_index = index
                if _matches_meal(meal, rule.get("keywords", [])):
                    return
                break
        candidates = list((plan.get("meal_candidate_pools") or {{}}).get(meal_type) or [])
        fallback_candidates = list(fallback_foods or [])
        all_candidates = candidates + [food for food in fallback_candidates if food not in candidates]
        preferred = [food for food in all_candidates if _matches_preference(food, rule.get("keywords", []))]
        if preferred:
            replacement_food = preferred[0]
            replacement_meal = self._build_personalized_meal(meal_type, replacement_food, request, plan, rule)
            if used_food_keys is not None:
                used_food_keys.add(self._food_key(replacement_food))
        else:
            return
        if target_index is None:
            meals.append(replacement_meal)
        else:
            meals[target_index] = replacement_meal
        plan["meals"] = meals
        meal_label = "午餐" if meal_type == "lunch" else "晚餐"
        plan.setdefault("notes", []).append("【个性化】已将第" + str(day) + "天" + meal_label + "优先安排为" + str(rule.get("label", "")) + "。")

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        runtime_plans = []
        for plan in daily_spot_plans:
            cloned = self._clone_runtime_plan(plan)
            self._apply_candidate_priorities(cloned)
            runtime_plans.append(cloned)
        result = super().attach_meals(request, runtime_plans, food_options, used_food_keys)
        for plan in result:
            for rule in RULES:
                self._enforce_rule_on_plan(request, plan, rule, used_food_keys, food_options)
        return result

import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _build_food_rules(self, task_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cuisine_specs = [
            ("hotpot", "火锅", ["火锅", "hotpot", "huoguo"]),
            ("bbq", "烧烤", ["烧烤", "烤肉", "bbq", "barbecue", "grill"]),
            ("dessert", "咖啡甜品", ["咖啡", "甜品", "cafe", "dessert", "蛋糕"]),
            ("seafood", "海鲜", ["海鲜", "seafood", "鱼", "虾", "蟹"]),
            ("local", "本地风味", ["本帮", "本地", "老字号", "local", "traditional"]),
            ("japanese", "日料", ["日料", "日本料理", "寿司", "刺身", "烧鸟", "sushi"]),
            ("korean", "韩料", ["韩料", "韩国料理", "韩餐", "部队锅"]),
            ("noodle", "面食", ["面", "面食", "面馆", "拉面", "拌面", "米线", "面条", "noodle"]),
        ]
        rules: list[dict[str, Any]] = []
        for item in task_items:
            text = str(item.get("text") or "").strip()
            lowered = text.lower()
            meal = self._extract_meal_type(item)
            if meal is None:
                if any(token in text for token in ["早饭", "早餐", "早上"]):
                    continue
                meal = "dinner"
            matched_keywords: list[str] = []
            for _, _, keywords in cuisine_specs:
                if any(keyword.lower() in lowered or keyword in text for keyword in keywords):
                    matched_keywords = keywords
                    break
            if not matched_keywords:
                continue
            rules.append(
                {
                    "day": self._extract_day_number(item),
                    "meal": meal,
                    "label": text,
                    "keywords": matched_keywords,
                }
            )
        return rules

    def _task_items(self, task: dict[str, Any], code_plan: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(code_plan.get("task_items"), list):
            return [item for item in code_plan["task_items"] if isinstance(item, dict)]
        return [
            {
                "id": task.get("id"),
                "text": task.get("text", ""),
                "scope": task.get("scope", {}),
                "expected_behavior": code_plan.get("expected_behavior", task.get("text", "")),
            }
        ]

    def _extract_day_number(self, task_item: dict[str, Any]) -> int | None:
        scope = task_item.get("scope") if isinstance(task_item.get("scope"), dict) else {}
        day_values = scope.get("days") if isinstance(scope.get("days"), list) else []
        candidates = [str(value) for value in day_values]
        if not candidates:
            candidates = [str(task_item.get("text") or "")]
        mapping = {
            "第一天": 1,
            "第二天": 2,
            "第三天": 3,
            "第四天": 4,
            "第五天": 5,
            "第六天": 6,
            "第七天": 7,
            "day1": 1,
            "day2": 2,
            "day3": 3,
            "day4": 4,
            "day5": 5,
            "day6": 6,
            "day7": 7,
        }
        for candidate in candidates:
            lowered = candidate.lower()
            for token, value in mapping.items():
                if token.lower() in lowered:
                    return value
        return None

    def _is_valid_extension(self, code: str, resolution: dict[str, Any]) -> bool:
        target_agent = str(resolution.get("target_agent") or "").strip()
        target_method = str(resolution.get("target_method") or "").strip()
        expected_class = {
            "food_spot": "FoodSpotAgent",
            "transport": "TransportAgent",
            "planner": "PlannerAgent",
            "hotel": "HotelAgent",
            "budget": "BudgetAgent",
            "search": "SearchAgent",
        }.get(target_agent)
        if not expected_class or not target_method:
            return False
        if "super()." not in code or expected_class not in code or f"def {target_method}" not in code:
            return False
        try:
            ast.parse(code)
        except SyntaxError:
            return False
        return True

    def _extract_meal_type(self, task_item: dict[str, Any]) -> str | None:
        scope = task_item.get("scope") if isinstance(task_item.get("scope"), dict) else {}
        meal_values = scope.get("meals") if isinstance(scope.get("meals"), list) else []
        text = str(task_item.get("text") or "")
        for meal in [str(value) for value in meal_values] + [text]:
            lowered = meal.lower()
            if any(token in meal for token in ["早饭", "早餐", "早上", "上午"]) or any(
                token in lowered for token in ["breakfast", "morning"]
            ):
                return "lunch"
            if any(token in meal for token in ["午饭", "午餐", "中午"]) or "lunch" in lowered:
                return "lunch"
            if any(token in meal for token in ["晚饭", "晚餐", "晚上", "夜宵", "宵夜"]) or any(
                token in lowered for token in ["dinner", "supper"]
            ):
                return "dinner"
        return None

    def _super_call_example(self, target_agent: str, target_method: str) -> str:
        mapping = {
            "food_spot": "return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)",
            "planner": "return super().create_daily_spot_plan(request, ranked_pois, constraints, llm_provider, policy)",
            "transport": "return super().build_day_transport(request, profile, day_spots, hotel, segments, day, total_days)",
            "hotel": "return super().attach_hotels(request, daily_spot_plans, hotel_options, llm_provider, policy)",
            "budget": "return super().build_budget(request, profile, day_plans, round_trip_transport_cost, round_trip_note)",
            "search": "return super().rank_pois(pois, request, constraints)",
        }
        return mapping.get(target_agent, f"return super().{target_method}(...)")

    def _target_signature(self, resolution: dict[str, Any]) -> str:
        return f"{resolution.get('target_agent', '')}.{resolution.get('target_method', '')}"

    def _build_patch(
        self,
        requirement: ParsedRequirement,
        task: dict[str, Any],
        resolution: dict[str, Any],
        code_plan: dict[str, Any],
        code: str,
        source: str,
    ) -> ModificationPatch:
        target_agent = str(code_plan.get("target_agent") or resolution.get("target_agent") or "").strip()
        target_method = str(code_plan.get("target_method") or resolution.get("target_method") or "").strip()
        task_key = self._task_patch_key(task, code_plan)
        req_hash = hashlib.md5(f"{requirement.raw_text}|{target_agent}|{task_key}".encode("utf-8")).hexdigest()[:10]
        filename = f"{source}_{target_agent}_{req_hash}.py"
        patch = FilePatch(
            file_path=f"personalization/extensions/{filename}",
            operation=PatchOperation.CREATE,
            original_snippet="",
            new_snippet=code,
            diff_lines=self.modifier._compute_diff("", code),
            metadata={
                "target_agent": target_agent,
                "target_method": target_method,
                "generated_by": source,
                "code_plan": code_plan,
                "requirement": requirement.raw_text,
                "feature": requirement.raw_text[:40],
                "task_key": task_key,
            },
        )
        return ModificationPatch(
            patches=[patch],
            metadata=PatchMetadata(description=f"{source} extension for: {requirement.raw_text}"),
            requirement_id=requirement.requirement_id,
        )

    def _task_patch_key(self, task: dict[str, Any], code_plan: dict[str, Any]) -> str:
        task_items = self._task_items(task, code_plan)
        if not task_items:
            return str(task.get("id") or "task")
        return "__".join(str(item.get("id") or "task") for item in task_items)
