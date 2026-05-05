"""Code Modifier Agent - generates code modification patches via LLM-powered extension files."""

from __future__ import annotations

import difflib
import importlib
import json
import logging
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import (
    FilePatch,
    ModificationPatch,
    ModificationType,
    ParsedRequirement,
    PatchMetadata,
    PatchOperation,
)

# Setup personalization logger
_personalization_log = logging.getLogger("personalization")
_personalization_log.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
if not _personalization_log.handlers:
    _personalization_log.addHandler(_handler)

RUNTIME_AGENT_SPECS: dict[str, tuple[str, str]] = {
    "food_spot": ("travel_multi_agent_planner.agents.food_spot", "FoodSpotAgent"),
    "transport": ("travel_multi_agent_planner.agents.transport", "TransportAgent"),
    "planner": ("travel_multi_agent_planner.agents.planner", "PlannerAgent"),
    "hotel": ("travel_multi_agent_planner.agents.hotel", "HotelAgent"),
    "budget": ("travel_multi_agent_planner.agents.budget", "BudgetAgent"),
    "search": ("travel_multi_agent_planner.agents.search", "SearchAgent"),
    "requirement": ("travel_multi_agent_planner.agents.requirement", "RequirementAgent"),
    "validator": ("travel_multi_agent_planner.agents.validator", "ConstraintValidatorAgent"),
}

ORIGINAL_RUNTIME_AGENT_CLASSES: dict[str, type] = {}
for _agent_name, (_module_path, _class_name) in RUNTIME_AGENT_SPECS.items():
    try:
        _module = importlib.import_module(_module_path)
        _cls = getattr(_module, _class_name, None)
        if _cls is not None:
            ORIGINAL_RUNTIME_AGENT_CLASSES[_agent_name] = _cls
    except Exception:
        continue


def ensure_extension_runtime(module: Any) -> None:
    """Inject shared globals required by generated extensions."""
    if getattr(module, "_personalization_log", None) is None:
        setattr(module, "_personalization_log", _personalization_log)


def unload_extension_modules() -> list[str]:
    """Drop imported personalization extension modules from the import cache."""
    removed: list[str] = []
    for module_name in list(sys.modules.keys()):
        if not module_name.startswith("personalization.extensions."):
            continue
        if module_name == "personalization.extensions":
            continue
        removed.append(module_name)
        del sys.modules[module_name]
    if removed:
        importlib.invalidate_caches()
    return removed


def sync_runtime_agent_bindings(target_agent: str | None = None) -> None:
    """
    Sync patched submodule classes back into aggregate imports used by the app.

    Extensions patch submodules such as `travel_multi_agent_planner.agents.planner`,
    while the rest of the app often imports from `travel_multi_agent_planner.agents`
    or captures those symbols inside `travel_multi_agent_planner.orchestrator`.
    """
    try:
        import travel_multi_agent_planner.agents as agents_pkg
        import travel_multi_agent_planner.orchestrator as orchestrator_mod
    except Exception:
        return

    selected_agents = [target_agent] if target_agent else list(RUNTIME_AGENT_SPECS.keys())
    for agent_name in selected_agents:
        spec = RUNTIME_AGENT_SPECS.get(agent_name)
        if spec is None:
            continue
        module_path, class_name = spec
        try:
            module = importlib.import_module(module_path)
            patched_class = getattr(module, class_name, None)
            if patched_class is None:
                continue
            setattr(agents_pkg, class_name, patched_class)
            if hasattr(orchestrator_mod, class_name):
                setattr(orchestrator_mod, class_name, patched_class)
        except Exception:
            continue


def _extract_requirement_from_extension_code(code: str, fallback_name: str) -> str:
    """Best-effort extraction of the original user requirement from an extension file."""
    match = re.search(r'Personalized agent for:\s*(.*?)"""', code, re.DOTALL)
    if match:
        value = re.sub(r"\s+", " ", match.group(1)).strip()
        if value:
            return value
    return fallback_name


def _infer_target_agent_from_extension_code(code: str) -> str | None:
    """Infer the target agent from an extension's imports or overridden methods."""
    target_markers = {
        "food_spot": ["agents.food_spot", "CustomFoodSpotAgent", "attach_meals"],
        "transport": ["agents.transport", "CustomTransportAgent", "build_day_transport"],
        "planner": ["agents.planner", "CustomPlannerAgent", "create_daily_spot_plan"],
        "hotel": ["agents.hotel", "CustomHotelAgent", "attach_hotels", "select_hotel"],
        "budget": ["agents.budget", "CustomBudgetAgent", "build_budget", "calculate_budget"],
        "search": ["agents.search", "CustomSearchAgent", "rank_pois"],
    }
    for target_agent, markers in target_markers.items():
        if any(marker in code for marker in markers):
            return target_agent
    return None


def upgrade_saved_extension_file(file_path: Path) -> bool:
    """
    Upgrade persisted extensions to the current runtime API before importing them.

    Older saved extensions used obsolete agent method signatures. Rewriting them
    at load time avoids startup-time and runtime crashes without requiring the
    user to manually delete their saved personalization files.
    """
    if not file_path.exists() or file_path.suffix != ".py":
        return False

    original_code = file_path.read_text(encoding="utf-8")
    modifier = CodeModifierAgent()
    requirement_text = _extract_requirement_from_extension_code(original_code, file_path.stem)
    target_agent = _infer_target_agent_from_extension_code(original_code)
    if target_agent is None and requirement_text:
        target_agent, _, _ = modifier._detect_target(requirement_text)

    upgraded_code = original_code
    if target_agent == "planner":
        upgraded_code = modifier._build_planner_runtime_extension(requirement_text)
    elif target_agent == "transport":
        upgraded_code = modifier._build_transport_runtime_extension(requirement_text)
    elif target_agent == "hotel":
        upgraded_code = modifier._build_hotel_runtime_extension(requirement_text)
    elif target_agent == "search":
        upgraded_code = modifier._build_search_runtime_extension(requirement_text)
    elif target_agent == "budget":
        upgraded_code = modifier._build_budget_runtime_extension(requirement_text)

    upgraded_code = modifier._finalize_extension_code(upgraded_code)
    if upgraded_code == original_code:
        return False

    file_path.write_text(upgraded_code, encoding="utf-8")
    return True


class CodeModifierAgent:
    """
    Generates code modification patches based on parsed requirements.

    This agent generates EXTENSION FILES in personalization/extensions/
    that use monkey patching to modify behavior at runtime, rather than
    modifying source code directly.

    All modifications are CODE-based - the LLM generates Python extension
    classes that override agent methods.
    """

    # Track original agent classes before any patching (for unload support)
    _original_agent_classes: dict[str, type] = {}

    # Track which extensions are currently loaded for each agent
    _loaded_extensions: dict[str, set[str]] = {}

    # Agent module path mappings
    AGENT_MODULE_MAP = {agent_name: spec[0] for agent_name, spec in RUNTIME_AGENT_SPECS.items()}

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider

    def generate(self, requirement: ParsedRequirement, base_path: Path) -> ModificationPatch:
        """
        Generate a modification patch based on the parsed requirement.

        Falls back to template-based generation if LLM fails.
        """
        patches: list[FilePatch] = []

        # Try LLM first
        llm_code = self._generate_extension_with_llm(requirement, base_path)
        if llm_code:
            patches.extend(llm_code)
        else:
            # Fallback to template-based generation
            print("DEBUG: LLM failed, using controlled rule generation")
            template_code = self._post_process_code("", requirement)
            if template_code and len(template_code) > 50:
                template_code = self._fix_imports(template_code)
                template_code = self._finalize_extension_code(template_code, requirement)
                import hashlib
                req_hash = hashlib.md5(requirement.raw_text.encode()).hexdigest()[:8]
                filename = f"template_extension_{req_hash}.py"
                target_agent, target_method, _ = self._detect_target(requirement.raw_text)

                patches.append(FilePatch(
                    file_path=f"personalization/extensions/{filename}",
                    operation=PatchOperation.CREATE,
                    original_snippet="",
                    new_snippet=template_code,
                    diff_lines=self._compute_diff("", template_code),
                    metadata={
                        "feature": requirement.raw_text[:30].replace(" ", "_").replace(",", "").replace("，", ""),
                        "target_agent": target_agent,
                        "target_method": target_method,
                        "generated_by": "template",
                        "requirement": requirement.raw_text,
                    },
                ))

        return ModificationPatch(
            patches=patches,
            metadata=PatchMetadata(description="Extension patch for: " + requirement.raw_text),
            requirement_id=requirement.requirement_id,
        )

    def _generate_extension_with_llm(
        self, requirement: ParsedRequirement, base_path: Path
    ) -> list[FilePatch]:
        """
        Use LLM to generate extension code for ANY requirement.
        """
        if not self.llm or not (hasattr(self.llm, "generate_code") or hasattr(self.llm, "_chat_text")):
            print(f"DEBUG: LLM not available")
            return []

        # Get comprehensive context about available agents and their methods
        agent_context = self._get_agent_context()

        # Build prompt
        prompt = (
            "You are a Python code generator for travel planning personalization.\n\n"
            "## User Requirement\n"
            + requirement.raw_text
            + "\n\n"
            + agent_context
            + "\n\n"
            "## IMPORTANT: Generate Simple Code That Calls super()\n\n"
            "The BEST approach is to override the method and call super() with modified arguments:\n\n"
            "```python\n"
            "from __future__ import annotations\n"
            "from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent\n\n"
            "class CustomFoodSpotAgent(FoodSpotAgent):\n"
            '    """Personalized agent for: ' + requirement.raw_text[:40] + '"""\n\n'
            "    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):\n"
            "        # Filter food_options to prioritize user's preference\n"
            "        if food_options:\n"
            "            # Your filter logic here, e.g.:\n"
            "            # hotpot = [f for f in food_options if '火锅' in f.name]\n"
            "            # if hotpot:\n"
            "            #     food_options = hotpot + [f for f in food_options if f not in hotpot]\n"
            "            pass\n"
            "        # Call parent - this is the key!\n"
            "        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)\n\n"
            "# Monkey patch\n"
            "import travel_multi_agent_planner.agents.food_spot\n"
            "travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent\n"
            "```\n\n"
            "## Output: ONLY Python code, no markdown, no explanation\n"
        )

        try:
            if hasattr(self.llm, "generate_code"):
                code = self.llm.generate_code(
                    system_prompt="You are a Python code generator. Return ONLY Python code, no markdown formatting.",
                    user_payload=prompt,
                )
            else:
                code = self.llm._chat_text(
                    system_prompt="You are a Python code generator. Return ONLY Python code, no markdown formatting.",
                    user_prompt=prompt,
                )
            print(f"DEBUG: LLM returned code of length {len(code) if code else 0}")
            if not code:
                return []

            # Clean up the code - remove all markdown formatting
            code = code.strip()
            code = re.sub(r'```python\s*', '', code)
            code = re.sub(r'```\s*', '', code)
            code = code.strip()

            if not code or len(code) < 50:
                print(f"LLM generated code too short or empty: {code[:100] if code else 'empty'}")
                return []

            # Post-process the code to simplify it
            code = self._post_process_code(code, requirement)

            # Ensure runtime-safe extension code
            code = self._finalize_extension_code(code, requirement)

            # Generate filename
            import hashlib
            req_hash = hashlib.md5(requirement.raw_text.encode()).hexdigest()[:8]
            filename = f"llm_extension_{req_hash}.py"

            # Detect target agent and method
            target_agent, target_method, _ = self._detect_target(requirement.raw_text)
            feature_name = requirement.raw_text[:30].replace(" ", "_").replace(",", "").replace("，", "")

            return [
                FilePatch(
                    file_path=f"personalization/extensions/{filename}",
                    operation=PatchOperation.CREATE,
                    original_snippet="",
                    new_snippet=code,
                    diff_lines=self._compute_diff("", code),
                    metadata={
                        "feature": feature_name,
                        "target_agent": target_agent,
                        "target_method": target_method,
                        "generated_by": "llm",
                        "requirement": requirement.raw_text,
                    },
                )
            ]
        except Exception as e:
            print(f"LLM code generation failed: {e}")
            return []

    def _finalize_extension_code(self, code: str, requirement: ParsedRequirement | None = None) -> str:
        """Normalize generated extension code to the current runtime API."""
        if requirement is not None:
            code = self._rewrite_runtime_safe_extension(code, requirement)

        code = self._fix_imports(code)

        if "_personalization_log" in code and "_personalization_log =" not in code:
            runtime_lines = [
                'import logging',
                '',
                '_personalization_log = logging.getLogger("personalization")',
                '',
            ]
            future_import = "from __future__ import annotations"
            if future_import in code:
                prefix, suffix = code.split(future_import, 1)
                code = f"{prefix}{future_import}\n\n" + "\n".join(runtime_lines) + suffix.lstrip("\n")
            else:
                code = "\n".join(runtime_lines) + code

        if not code.endswith("\n"):
            code += "\n"
        return code

    def _rewrite_runtime_safe_extension(self, code: str, requirement: ParsedRequirement) -> str:
        """Replace stale non-food templates with signatures that match the current agents."""
        target_agent, _, _ = self._detect_target(requirement.raw_text)
        if target_agent == "planner":
            return self._build_planner_runtime_extension(requirement.raw_text)
        if target_agent == "transport":
            return self._build_transport_runtime_extension(requirement.raw_text)
        if target_agent == "hotel":
            return self._build_hotel_runtime_extension(requirement.raw_text)
        if target_agent == "search":
            return self._build_search_runtime_extension(requirement.raw_text)
        if target_agent == "budget":
            return self._build_budget_runtime_extension(requirement.raw_text)
        return code

    def _build_planner_runtime_extension(self, requirement: str) -> str:
        req_lower = requirement.lower()
        style_override = None
        if any(word in req_lower for word in ["relaxed", "轻松", "放松"]):
            style_override = "relaxed"
        elif any(word in req_lower for word in ["dense", "紧凑", "密集"]):
            style_override = "dense"

        messages: list[str] = []
        if style_override == "relaxed":
            messages.append("已按更轻松的节奏降低每日景点密度")
        elif style_override == "dense":
            messages.append("已按更紧凑的节奏提高每日景点密度")
        if any(word in req_lower for word in ["spread", "分散", "均匀"]):
            messages.append("已提醒优先拉开景点分布，避免长时间停留在单一区域")
        if any(word in req_lower for word in ["cluster", "集中", "聚集"]):
            messages.append("已提醒优先聚合同一区域景点，减少跨区折返")
        if any(word in req_lower for word in ["morning", "上午", "早上"]):
            messages.append("已提醒把核心活动尽量放在上午完成")
        if any(word in req_lower for word in ["evening", "晚上", "傍晚"]):
            messages.append("已提醒保留傍晚和夜间活动空间")
        if any(word in req_lower for word in ["crowd", "避开", "避开", "拥挤"]):
            messages.append("已提醒尽量规避拥挤景点和高峰时段")
        if any(word in req_lower for word in ["photogenic", "photo", "拍照", "好看"]):
            messages.append("已提醒优先保留更适合拍照的景点")
        if not messages:
            messages.append("已应用行程规划个性化偏好")

        message_text = "【个性化】" + "；".join(messages)
        style_block = "        runtime_request = request\n"
        if style_override:
            style_block = (
                "        runtime_request = copy.copy(request)\n"
                f"        runtime_request.style = {json.dumps(style_override, ensure_ascii=False)}\n"
            )

        return f'''from __future__ import annotations
import copy
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, ranked_pois, constraints, llm_provider=None, policy=None):
        _personalization_log.info("Applying planner personalization")
{style_block}        daily_plans, planning_note = super().create_daily_spot_plan(
            runtime_request, ranked_pois, constraints, llm_provider, policy
        )
        for plan in daily_plans:
            notes = list(plan.get("notes", []))
            notes.append({json.dumps(message_text, ensure_ascii=False)})
            plan["notes"] = notes
        summary = {json.dumps(message_text, ensure_ascii=False)}
        planning_note = f"{{planning_note}}；{{summary}}" if planning_note else summary
        return daily_plans, planning_note

import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _build_transport_runtime_extension(self, requirement: str) -> str:
        req_lower = requirement.lower()
        message = "已应用交通出行个性化偏好"
        if any(word in req_lower for word in ["parking", "停车"]):
            message = "已在交通说明中加入停车便利提醒"
        elif any(word in req_lower for word in ["car", "drive", "自驾", "开车"]):
            message = "已优先提示自驾和停车相关注意事项"
        elif any(word in req_lower for word in ["public transit", "metro", "bus", "地铁", "公交"]):
            message = "已优先提示公共交通换乘方案"
        elif any(word in req_lower for word in ["walk", "walking", "步行"]):
            message = "已优先提示步行串联景点"
        elif any(word in req_lower for word in ["bike", "bicycling", "骑行"]):
            message = "已优先提示骑行串联景点"
        note_text = "【个性化】" + message

        return f'''from __future__ import annotations
import travel_multi_agent_planner.agents.transport as transport_module
from travel_multi_agent_planner.agents.transport import TransportAgent

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_day_transport(self, request, profile, day_spots, hotel, segments, day, total_days):
        _personalization_log.info("Applying transport personalization for day %s", day)
        result = super().build_day_transport(request, profile, day_spots, hotel, segments, day, total_days)
        note = {json.dumps(note_text, ensure_ascii=False)}
        result.intra_city = f"{{result.intra_city}} {{note}}".strip()
        result.route_summary = f"{{result.route_summary}}；{{note}}" if result.route_summary else note
        return result

transport_module.TransportAgent = CustomTransportAgent
'''

    def _build_hotel_runtime_extension(self, requirement: str) -> str:
        req_lower = requirement.lower()
        budget_override = None
        message = "已应用酒店选择个性化偏好"
        if any(word in req_lower for word in ["luxury", "premium", "豪华", "高端"]):
            budget_override = "premium"
            message = "已按更高端的酒店预算偏好筛选住宿"
        elif any(word in req_lower for word in ["station", "车站", "火车站", "地铁站"]):
            message = "已在酒店说明中加入靠近站点的优先提醒"

        request_block = "        runtime_request = request\n"
        if budget_override:
            request_block = (
                "        runtime_request = copy.copy(request)\n"
                f"        runtime_request.hotel_budget_preference = {json.dumps(budget_override, ensure_ascii=False)}\n"
            )
        note_text = "【个性化】" + message

        return f'''from __future__ import annotations
import copy
from travel_multi_agent_planner.agents.hotel import HotelAgent

class CustomHotelAgent(HotelAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_hotels(self, request, daily_spot_plans, hotel_options, llm_provider=None, policy=None):
        _personalization_log.info("Applying hotel personalization")
{request_block}        updated_plans, notes = super().attach_hotels(
            runtime_request, daily_spot_plans, hotel_options, llm_provider, policy
        )
        notes = list(notes)
        notes.append({json.dumps(note_text, ensure_ascii=False)})
        return updated_plans, notes

import travel_multi_agent_planner.agents.hotel
travel_multi_agent_planner.agents.hotel.HotelAgent = CustomHotelAgent
'''

    def _build_search_runtime_extension(self, requirement: str) -> str:
        req_lower = requirement.lower()
        if any(word in req_lower for word in ["hidden", "小众", "冷门", "秘境"]):
            ranking_code = (
                "        return sorted(\n"
                "            ranked,\n"
                "            key=lambda poi: (\n"
                "                -len(getattr(poi, 'source_evidence', []) or []),\n"
                "                poi.ticket_cost,\n"
                "                poi.duration_hours,\n"
                "            ),\n"
                "        )\n"
            )
        else:
            ranking_code = (
                "        return sorted(\n"
                "            ranked,\n"
                "            key=lambda poi: (\n"
                "                len(getattr(poi, 'source_evidence', []) or []),\n"
                "                -poi.ticket_cost,\n"
                "                poi.duration_hours,\n"
                "            ),\n"
                "            reverse=True,\n"
                "        )\n"
            )

        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.search import SearchAgent

class CustomSearchAgent(SearchAgent):
    """Personalized agent for: {requirement[:50]}"""

    def rank_pois(self, pois, request, constraints):
        _personalization_log.info("Applying search personalization to %s POIs", len(pois))
        ranked = super().rank_pois(pois, request, constraints)
{ranking_code}
import travel_multi_agent_planner.agents.search
travel_multi_agent_planner.agents.search.SearchAgent = CustomSearchAgent
'''

    def _build_budget_runtime_extension(self, requirement: str) -> str:
        req_lower = requirement.lower()
        preference = "balanced"
        message = "已应用预算个性化偏好"
        if any(word in req_lower for word in ["economical", "budget", "省钱", "经济"]):
            preference = "budget"
            message = "已按更节省的预算偏好估算餐饮和住宿"
        elif any(word in req_lower for word in ["premium", "豪华", "高端"]):
            preference = "premium"
            message = "已按更高端的预算偏好估算餐饮和住宿"

        note_text = "【个性化】" + message

        return f'''from __future__ import annotations
import copy
from travel_multi_agent_planner.agents.budget import BudgetAgent
from travel_multi_agent_planner.models import BudgetLine

class CustomBudgetAgent(BudgetAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_budget(self, request, profile, day_plans, round_trip_transport_cost, round_trip_note):
        _personalization_log.info("Applying budget personalization")
        runtime_request = copy.copy(request)
        runtime_request.food_budget_preference = {json.dumps(preference, ensure_ascii=False)}
        runtime_request.hotel_budget_preference = {json.dumps(preference, ensure_ascii=False)}
        summary = super().build_budget(
            runtime_request, profile, day_plans, round_trip_transport_cost, round_trip_note
        )
        summary.lines = list(summary.lines) + [
            BudgetLine("个性化偏好", 0.0, {json.dumps(note_text, ensure_ascii=False)})
        ]
        return summary

import travel_multi_agent_planner.agents.budget
travel_multi_agent_planner.agents.budget.BudgetAgent = CustomBudgetAgent
'''

    def _post_process_code(self, code: str, requirement: ParsedRequirement) -> str:
        """
        Post-process LLM-generated code to simplify and fix common issues.
        For food-related requirements, ALWAYS use the simple template.
        """
        target_agent, target_method, _ = self._detect_target(requirement.raw_text)
        req_lower = requirement.raw_text.lower()

        _personalization_log.info(f"Post-processing requirement: '{requirement.raw_text[:50]}' (target: {target_agent})")

        if code.strip() and self._looks_like_valid_extension(code, target_agent, target_method):
            return self._fix_imports(code)

        # For food-related requirements, always use the simple template
        if target_agent == "food_spot":
            _personalization_log.info(f"Food requirement detected - checking for multiple preferences")

            # Detect all food preferences in the requirement
            food_prefs = []

            # Check for hotpot
            if any(w in req_lower for w in ['火锅', 'hotpot', 'huoguo', 'hot pot']):
                food_prefs.append({'name': 'hotpot', 'keywords': ['火锅', 'hotpot', 'huoguo', '麻辣烫']})

            # Check for Japanese food
            if any(w in req_lower for w in ['日本', '日料', '寿司', '刺身', 'japanese']):
                food_prefs.append({'name': 'japanese', 'keywords': ['日本', '日料', '寿司', '刺身']})

            # Check for Sichuan food
            if any(w in req_lower for w in ['川菜', '川', '麻辣', 'sichuan']):
                food_prefs.append({'name': 'sichuan', 'keywords': ['川菜', '川', '麻辣']})

            # Check for Cantonese food
            if any(w in req_lower for w in ['粤菜', '粤', '早茶', 'cantonese']):
                food_prefs.append({'name': 'cantonese', 'keywords': ['粤菜', '粤', '早茶']})

            # Check for vegetarian
            if any(w in req_lower for w in ['素', '斋', 'vegetarian', 'vegan', '素食']):
                food_prefs.append({'name': 'vegetarian', 'keywords': ['素', '斋', 'vegetarian', 'vegan']})

            # Check for seafood
            if any(w in req_lower for w in ['海鲜', '鱼', '虾', 'seafood', 'fish', 'shrimp']):
                food_prefs.append({'name': 'seafood', 'keywords': ['海鲜', '鱼', '虾', '蟹', '贝']})

            # Check for cafe/dessert
            if any(w in req_lower for w in ['咖啡', 'cafe', '甜品', '蛋糕', '下午茶', 'dessert', 'tea']):
                food_prefs.append({'name': 'cafe', 'keywords': ['咖啡', 'cafe', '甜品', '蛋糕', '下午茶', '奶茶']})

            # Check for local cuisine
            if any(w in req_lower for w in ['本地', '地道', '老字号', 'local', 'traditional']):
                food_prefs.append({'name': 'local', 'keywords': ['本地', '地道', '老字号', '特色']})

            # If multiple food preferences detected, generate combined extension
            if len(food_prefs) > 1:
                _personalization_log.info(f"Generating multi-food preference extension with {len(food_prefs)} preferences")
                return self._generate_multi_food_preference_extension(requirement.raw_text, food_prefs)

            # Check for skip requests - these can combine with food preferences
            skip_meal = None
            if any(w in req_lower for w in ['不需要', '不要', '跳过', 'skip']):
                if '早餐' in requirement.raw_text or 'breakfast' in req_lower:
                    skip_meal = 'breakfast'
                elif '午餐' in requirement.raw_text or 'lunch' in req_lower:
                    skip_meal = 'lunch'
                elif '晚餐' in requirement.raw_text or 'dinner' in req_lower:
                    skip_meal = 'dinner'
                else:
                    skip_meal = 'lunch'  # default
                _personalization_log.info(f"Skip meal detected: {skip_meal}")

            # Single preference - use appropriate template
            if any(w in req_lower for w in ['火锅', 'hotpot', 'huoguo', 'hot pot']):
                _personalization_log.info(f"Generating hotpot extension")
                return self._generate_hotpot_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['日本', '日料', '寿司', '刺身', 'japanese']):
                _personalization_log.info(f"Generating Japanese food extension")
                return self._generate_cuisine_extension(requirement.raw_text, '日本', ['日本', '日料', '寿司', '刺身'])
            elif any(w in req_lower for w in ['川菜', '川', '麻辣', 'sichuan']):
                _personalization_log.info(f"Generating Sichuan food extension")
                return self._generate_cuisine_extension(requirement.raw_text, '川菜', ['川菜', '川', '麻辣', '火锅'])
            elif any(w in req_lower for w in ['粤菜', '粤', '早茶', 'cantonese']):
                _personalization_log.info(f"Generating Cantonese food extension")
                return self._generate_cuisine_extension(requirement.raw_text, '粤菜', ['粤菜', '粤', '早茶'])
            elif any(w in req_lower for w in ['素', '斋', 'vegetarian', 'vegan', '素食']):
                _personalization_log.info(f"Generating vegetarian extension")
                return self._generate_vegetarian_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['海鲜', '鱼', '虾', 'seafood', 'fish', 'shrimp']):
                _personalization_log.info(f"Generating seafood extension")
                return self._generate_seafood_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['咖啡', 'cafe', '甜品', '蛋糕', '下午茶', 'dessert', 'tea']):
                _personalization_log.info(f"Generating cafe extension")
                return self._generate_cafe_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['本地', '地道', '老字号', 'local', 'traditional']):
                _personalization_log.info(f"Generating local cuisine extension")
                return self._generate_local_cuisine_extension(requirement.raw_text)
            elif skip_meal:
                _personalization_log.info(f"Generating skip {skip_meal} extension")
                return self._generate_skip_meal_extension(requirement.raw_text, skip_meal)
            elif any(w in req_lower for w in ['餐厅', '美食', 'food', 'restaurant', '餐', '吃']):
                _personalization_log.info(f"Generating food preference extension")
                return self._generate_food_preference_extension(requirement.raw_text)
            else:
                _personalization_log.info(f"Generating default food preference extension")
                return self._generate_food_preference_extension(requirement.raw_text)

        # For planner-related requirements
        elif target_agent == "planner":
            _personalization_log.info(f"Planner requirement detected - using simple template")
            if any(w in req_lower for w in ['分散', 'spread', '均匀']):
                _personalization_log.info(f"Generating spread spots extension")
                return self._generate_spread_spots_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['集中', 'cluster', '聚集']):
                _personalization_log.info(f"Generating cluster spots extension")
                return self._generate_cluster_spots_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['放松', '轻松', 'relaxed']):
                _personalization_log.info(f"Generating relaxed pacing extension")
                return self._generate_relaxed_pacing_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['紧凑', 'dense', '密集']):
                _personalization_log.info(f"Generating dense schedule extension")
                return self._generate_dense_schedule_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['上午', '早上', 'morning']):
                _personalization_log.info(f"Generating morning focus extension")
                return self._generate_morning_focus_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['晚上', '傍晚', 'evening']):
                _personalization_log.info(f"Generating evening focus extension")
                return self._generate_evening_focus_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['避开', '避免', '拥挤', 'crowd']):
                _personalization_log.info(f"Generating avoid crowds extension")
                return self._generate_avoid_crowds_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['拍照', '好看', 'photogenic', 'photo']):
                _personalization_log.info(f"Generating photogenic spots extension")
                return self._generate_photogenic_extension(requirement.raw_text)
            else:
                _personalization_log.info(f"Generating default planner extension")
                return self._generate_planner_extension(requirement.raw_text)

        # For transport-related requirements
        elif target_agent == "transport":
            _personalization_log.info(f"Transport requirement detected")
            if any(w in req_lower for w in ['开车', 'car', '自驾', 'drive']):
                _personalization_log.info(f"Generating car mode extension")
                return self._generate_car_mode_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['停车', 'parking']):
                _personalization_log.info(f"Generating parking extension")
                return self._generate_parking_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['地铁', '公交', 'public transit', 'bus', 'metro']):
                _personalization_log.info(f"Generating public transit extension")
                return self._generate_public_transit_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['步行', 'walking', 'walk']):
                _personalization_log.info(f"Generating walking extension")
                return self._generate_walking_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['骑行', 'bike', '自行车']):
                _personalization_log.info(f"Generating bike extension")
                return self._generate_bike_extension(requirement.raw_text)
            else:
                _personalization_log.info(f"Generating default transport extension")
                return self._generate_transport_extension(requirement.raw_text)

        # For budget-related requirements
        elif target_agent == "budget":
            _personalization_log.info(f"Budget requirement detected")
            if any(w in req_lower for w in ['省钱', 'economical', '经济', '穷游', '节约']):
                _personalization_log.info(f"Generating budget extension")
                return self._generate_budget_extension(requirement.raw_text, 'budget')
            elif any(w in req_lower for w in ['奢侈', '高端', 'premium', '豪华', '轻奢', '轻奢']):
                _personalization_log.info(f"Generating premium extension")
                return self._generate_budget_extension(requirement.raw_text, 'premium')
            else:
                _personalization_log.info(f"Generating balanced budget extension")
                return self._generate_budget_extension(requirement.raw_text, 'balanced')

        # For hotel-related requirements
        elif target_agent == "hotel":
            _personalization_log.info(f"Hotel requirement detected")
            if any(w in req_lower for w in ['车站', 'station', '火车站', '地铁站', '地铁', '交通']):
                _personalization_log.info(f"Generating near station hotel extension")
                return self._generate_near_station_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['豪华', 'luxury', '高档', '高端', '奢侈']):
                _personalization_log.info(f"Generating luxury hotel extension")
                return self._generate_luxury_hotel_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['安静', '安静', '清静']):
                _personalization_log.info(f"Generating quiet hotel extension")
                return self._generate_quiet_hotel_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['夜景', '夜生活', '酒吧', '夜店', 'nightlife']):
                _personalization_log.info(f"Generating nightlife hotel extension")
                return self._generate_nightlife_hotel_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['景区', '景区', '山', '湖', '海', '自然', '风景']):
                _personalization_log.info(f"Generating scenic hotel extension")
                return self._generate_scenic_hotel_extension(requirement.raw_text)
            else:
                _personalization_log.info(f"Generating default hotel extension")
                return self._generate_planner_extension(requirement.raw_text)

        # For search-related requirements
        elif target_agent == "search":
            _personalization_log.info(f"Search requirement detected")
            if any(w in req_lower for w in ['热门', 'popular', '有名', '著名', '网红']):
                _personalization_log.info(f"Generating popular spots extension")
                return self._generate_popular_spots_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['小众', 'hidden', '冷门', '秘密', '人少']):
                _personalization_log.info(f"Generating hidden gems extension")
                return self._generate_hidden_gems_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['拍照', '好看', '出片', '网红打卡', 'photogenic']):
                _personalization_log.info(f"Generating photography spots extension")
                return self._generate_photography_spots_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['夜生活', '酒吧', '夜游', '夜景', 'nightlife', '夜店']):
                _personalization_log.info(f"Generating nightlife spots extension")
                return self._generate_nightlife_spots_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['文化', '博物馆', '历史', '人文', '博物馆']):
                _personalization_log.info(f"Generating culture spots extension")
                return self._generate_culture_spots_extension(requirement.raw_text)
            elif any(w in req_lower for w in ['美食', '餐饮', '好吃', 'food', '吃']):
                _personalization_log.info(f"Generating food spots extension")
                return self._generate_food_spots_extension(requirement.raw_text)
            else:
                _personalization_log.info(f"Generating default search extension")
                return self._generate_popular_spots_extension(requirement.raw_text)

        # For non-food requirements, simplify if code is too complex
        manual_meal_count = code.count('MealRecommendation(')
        if manual_meal_count > 3:
            _personalization_log.warning(f"Code too complex ({manual_meal_count} MealRecommendation) - simplifying")
            return self._generate_food_preference_extension(requirement.raw_text)

        return code

    def _looks_like_valid_extension(self, code: str, target_agent: str, target_method: str) -> bool:
        """Allow already-correct LLM code to pass through without rewriting it to templates."""
        if "class " not in code or "super()." not in code:
            return False
        if target_method and f"def {target_method}" not in code:
            return False
        expected_class_map = {
            "food_spot": "FoodSpotAgent",
            "transport": "TransportAgent",
            "planner": "PlannerAgent",
            "hotel": "HotelAgent",
            "budget": "BudgetAgent",
            "search": "SearchAgent",
        }
        expected = expected_class_map.get(target_agent)
        if expected and expected not in code:
            return False
        return True

    def _generate_simple_food_extension(self, requirement: str, filter_func, preference_name: str) -> str:
        """Generate a simple extension that filters food options."""
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        if food_options:
            # Filter to prioritize {preference_name}
            preferred = [f for f in food_options if {filter_func.__name__}(f)]
            if preferred:
                # Put preferred options at front, keep others for fallback
                others = [f for f in food_options if f not in preferred]
                food_options = preferred + others
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

def {filter_func.__name__}(food):
    name_lower = food.name.lower() if hasattr(food, 'name') else ''
    cuisine_lower = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    return '火锅' in name_lower or 'hotpot' in name_lower or 'huoguo' in name_lower or '火锅' in cuisine_lower

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _generate_multi_food_preference_extension(self, requirement: str, food_prefs: list[dict]) -> str:
        """
        Generate extension for multiple food preferences in one requirement.

        Args:
            requirement: Original requirement text
            food_prefs: List of dicts with 'name', 'keywords' for each preference
        """
        if not food_prefs:
            return self._generate_food_preference_extension(requirement)

        # Build filter functions for each preference
        filter_funcs = []
        for pref in food_prefs:
            name = pref['name']
            keywords = pref['keywords']
            filter_funcs.append(f'''
def _is_{name}(food):
    """Check if food matches {name}."""
    name_str = food.name.lower() if hasattr(food, 'name') else ''
    cuisine = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    tags_str = ' '.join(food.tags).lower() if hasattr(food, 'tags') else ''
    keywords = {keywords}
    for kw in keywords:
        if kw.lower() in name_str or kw.lower() in cuisine or kw.lower() in tags_str:
            return True
    return False''')

        filter_funcs_str = '\n'.join(filter_funcs)

        # Build priority logic - collect all preferences first
        priority_vars = []
        priority_checks = []
        for pref in food_prefs:
            name = pref['name']
            priority_vars.append(f'{name}_pref = [f for f in food_options if _is_{name}(f)]')
            priority_checks.append(f'''if {name}_pref and not preferred:
                preferred = {name}_pref
                _personalization_log.info("{name} preference: {{len({name}_pref)}} options")''')

        priority_vars_str = '\n            '.join(priority_vars)
        priority_checks_str = '\n            '.join(priority_checks)

        _personalization_log.info(f"GENERATING multi-food preference extension for: {requirement[:50]}")

        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

{filter_funcs_str}

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying multi-food preference")
        if food_options:
            # Find preferred options based on user preferences
            {priority_vars_str}
            preferred = []
            {priority_checks_str}

            # If we found preferred options, put them first
            if preferred:
                others = [f for f in food_options if f not in preferred]
                food_options = preferred + others
                _personalization_log.info(f"Multi-food filter: {{len(preferred)}} preferred, {{len(others)}} fallback")
            else:
                # No specific preferences matched, use original order
                _personalization_log.info("No specific food preferences matched, using original order")

        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _generate_cuisine_extension(self, requirement: str, cuisine_name: str, keywords: list[str]) -> str:
        """Generate extension for specific cuisine preference."""
        _personalization_log.info(f"GENERATING {cuisine_name} cuisine extension for: {requirement[:50]}")
        keywords_str = str(keywords)
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

def _is_cuisine(food):
    """Check if a food venue matches {cuisine_name}."""
    name = food.name.lower() if hasattr(food, 'name') else ''
    cuisine = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    tags_str = ' '.join(food.tags).lower() if hasattr(food, 'tags') else ''
    keywords = {keywords_str}
    for kw in keywords:
        if kw.lower() in name or kw.lower() in cuisine or kw.lower() in tags_str:
            return True
    return False

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying {cuisine_name} cuisine preference - filtering food options")
        if food_options:
            # Filter to prioritize {cuisine_name}
            preferred = [f for f in food_options if _is_cuisine(f)]
            others = [f for f in food_options if not _is_cuisine(f)]
            if preferred:
                food_options = preferred + others
                _personalization_log.info(f"{cuisine_name} filter: {{len(preferred)}} preferred, {{len(others)}} fallback")
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _generate_skip_meal_extension(self, requirement: str, meal_type: str) -> str:
        """Generate extension that skips a specific meal type."""
        _personalization_log.info(f"GENERATING skip {meal_type} extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying skip {meal_type} preference")
        # Call parent to get normal meal assignment
        result = super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)
        # Remove the {meal_type} from each day's plan
        skipped = 0
        for plan in result:
            if 'meals' in plan:
                original_count = len(plan['meals'])
                plan['meals'] = [m for m in plan['meals'] if m.meal_type != "{meal_type}"]
                skipped += original_count - len(plan['meals'])
                # Add note about skipped meal
                notes = plan.setdefault('notes', [])
                notes.append("【个性化】用户不需要{meal_type}，已跳过")
        _personalization_log.info(f"Skipped {{skipped}} {meal_type} meals")
        return result

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _generate_hotpot_extension(self, requirement: str) -> str:
        """Generate extension for hotpot preference."""
        _personalization_log.info(f"GENERATING hotpot extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

def _is_hotpot(food):
    """Check if a food venue is a hotpot restaurant."""
    name = food.name.lower() if hasattr(food, 'name') else ''
    cuisine = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    return '火锅' in name or 'hotpot' in name or 'huoguo' in name or '麻辣烫' in name or '火锅' in cuisine

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying hotpot preference - filtering food options")
        if food_options:
            # Filter to prioritize hotpot
            hotpot = [f for f in food_options if _is_hotpot(f)]
            if hotpot:
                # Put hotpot first, others as fallback
                others = [f for f in food_options if not _is_hotpot(f)]
                food_options = hotpot + others
                _personalization_log.info(f"Hotpot filter: {{len(hotpot)}} hotpot restaurants, {{len(others)}} others")
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    # ============ Food Templates - Additional Cuisines ============

    def _generate_vegetarian_extension(self, requirement: str) -> str:
        """Generate extension for vegetarian preference."""
        _personalization_log.info(f"GENERATING vegetarian extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

def _is_vegetarian(food):
    """Check if a food venue is vegetarian-friendly."""
    name = food.name.lower() if hasattr(food, 'name') else ''
    cuisine = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    tags_str = ' '.join(food.tags).lower() if hasattr(food, 'tags') else ''
    for kw in ['素', '斋', 'vegetarian', 'vegan', '素食']:
        if kw.lower() in name or kw.lower() in cuisine or kw.lower() in tags_str:
            return True
    return False

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying vegetarian preference - filtering food options")
        if food_options:
            preferred = [f for f in food_options if _is_vegetarian(f)]
            others = [f for f in food_options if not _is_vegetarian(f)]
            if preferred:
                food_options = preferred + others
                _personalization_log.info(f"Vegetarian filter: {{len(preferred)}} preferred, {{len(others)}} fallback")
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _generate_seafood_extension(self, requirement: str) -> str:
        """Generate extension for seafood preference."""
        _personalization_log.info(f"GENERATING seafood extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

def _is_seafood(food):
    """Check if a food venue is seafood."""
    name = food.name.lower() if hasattr(food, 'name') else ''
    cuisine = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    tags_str = ' '.join(food.tags).lower() if hasattr(food, 'tags') else ''
    for kw in ['海鲜', '鱼', '虾', '蟹', '贝', 'seafood', 'fish', 'shrimp']:
        if kw.lower() in name or kw.lower() in cuisine or kw.lower() in tags_str:
            return True
    return False

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying seafood preference - filtering food options")
        if food_options:
            preferred = [f for f in food_options if _is_seafood(f)]
            others = [f for f in food_options if not _is_seafood(f)]
            if preferred:
                food_options = preferred + others
                _personalization_log.info(f"Seafood filter: {{len(preferred)}} preferred, {{len(others)}} fallback")
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _generate_cafe_extension(self, requirement: str) -> str:
        """Generate extension for cafe/dessert preference."""
        _personalization_log.info(f"GENERATING cafe extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

def _is_cafe(food):
    """Check if a food venue is a cafe or dessert shop."""
    name = food.name.lower() if hasattr(food, 'name') else ''
    cuisine = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    tags_str = ' '.join(food.tags).lower() if hasattr(food, 'tags') else ''
    for kw in ['咖啡', 'cafe', '甜品', '蛋糕', '下午茶', '奶茶', 'dessert', 'tea']:
        if kw.lower() in name or kw.lower() in cuisine or kw.lower() in tags_str:
            return True
    return False

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying cafe/dessert preference - filtering food options")
        if food_options:
            preferred = [f for f in food_options if _is_cafe(f)]
            others = [f for f in food_options if not _is_cafe(f)]
            if preferred:
                food_options = preferred + others
                _personalization_log.info(f"Cafe filter: {{len(preferred)}} preferred, {{len(others)}} fallback")
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    def _generate_local_cuisine_extension(self, requirement: str) -> str:
        """Generate extension for local cuisine preference."""
        _personalization_log.info(f"GENERATING local cuisine extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

def _is_local_cuisine(food):
    """Check if a food venue serves local cuisine."""
    name = food.name.lower() if hasattr(food, 'name') else ''
    cuisine = food.cuisine.lower() if hasattr(food, 'cuisine') else ''
    tags_str = ' '.join(food.tags).lower() if hasattr(food, 'tags') else ''
    for kw in ['本地', '地道', '老字号', '特色', 'local', 'traditional', 'authentic']:
        if kw.lower() in name or kw.lower() in cuisine or kw.lower() in tags_str:
            return True
    return False

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info("Applying local cuisine preference - filtering food options")
        if food_options:
            preferred = [f for f in food_options if _is_local_cuisine(f)]
            others = [f for f in food_options if not _is_local_cuisine(f)]
            if preferred:
                food_options = preferred + others
                _personalization_log.info(f"Local cuisine filter: {{len(preferred)}} preferred, {{len(others)}} fallback")
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    # ============ Planner Templates - Additional Scenarios ============

    def _generate_morning_focus_extension(self, requirement: str) -> str:
        """Generate extension to focus activities in the morning."""
        _personalization_log.info(f"GENERATING morning focus extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, ranked_pois, constraints, llm_provider=None):
        _personalization_log.info("Applying morning focus personalization")
        daily_plans, planning_note = super().create_daily_spot_plan(request, ranked_pois, constraints, llm_provider)
        for plan in daily_plans:
            notes = list(plan.get("notes", []))
            notes.append("【个性化】已侧重上午时段活动安排")
            plan["notes"] = notes
        return daily_plans, planning_note

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _generate_evening_focus_extension(self, requirement: str) -> str:
        """Generate extension to allow more evening activities."""
        _personalization_log.info(f"GENERATING evening focus extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, ranked_pois, constraints, llm_provider=None):
        _personalization_log.info("Applying evening focus personalization")
        daily_plans, planning_note = super().create_daily_spot_plan(request, ranked_pois, constraints, llm_provider)
        for plan in daily_plans:
            notes = list(plan.get("notes", []))
            notes.append("【个性化】已侧重晚间时段活动安排")
            plan["notes"] = notes
        return daily_plans, planning_note

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _generate_avoid_crowds_extension(self, requirement: str) -> str:
        """Generate extension to avoid crowded spots."""
        _personalization_log.info(f"GENERATING avoid crowds extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, ranked_pois, constraints, llm_provider=None):
        _personalization_log.info("Applying avoid crowds personalization")
        daily_plans, planning_note = super().create_daily_spot_plan(request, ranked_pois, constraints, llm_provider)
        for plan in daily_plans:
            notes = list(plan.get("notes", []))
            notes.append("【个性化】已考虑避开拥挤景点")
            plan["notes"] = notes
        return daily_plans, planning_note

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _generate_photogenic_extension(self, requirement: str) -> str:
        """Generate extension to prioritize photogenic spots."""
        _personalization_log.info(f"GENERATING photogenic spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, ranked_pois, constraints, llm_provider=None):
        _personalization_log.info("Applying photogenic spots personalization")
        daily_plans, planning_note = super().create_daily_spot_plan(request, ranked_pois, constraints, llm_provider)
        for plan in daily_plans:
            notes = list(plan.get("notes", []))
            notes.append("【个性化】已优先安排拍照好看的景点")
            plan["notes"] = notes
        return daily_plans, planning_note

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    # ============ Transport Templates - Additional Modes ============

    def _generate_public_transit_extension(self, requirement: str) -> str:
        """Generate extension for public transit preference."""
        _personalization_log.info(f"GENERATING public transit extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.transport import TransportAgent

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_day_transport(self, request, profile, day_spots, hotel, segments, day, total_days):
        _personalization_log.info(f"Applying public transit preference for day {{day}}")
        return super().build_day_transport(request, profile, day_spots, hotel, segments, day, total_days)

# Monkey patch
import travel_multi_agent_planner.agents.transport
travel_multi_agent_planner.agents.transport.TransportAgent = CustomTransportAgent
'''

    def _generate_walking_extension(self, requirement: str) -> str:
        """Generate extension for walking preference."""
        _personalization_log.info(f"GENERATING walking extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.transport import TransportAgent

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_day_transport(self, request, profile, day_spots, hotel, segments, day, total_days):
        _personalization_log.info(f"Applying walking preference for day {{day}}")
        result = super().build_day_transport(request, profile, day_spots, hotel, segments, day, total_days)
        result.intra_city = result.intra_city + " 建议以步行为主。"
        return result

# Monkey patch
import travel_multi_agent_planner.agents.transport
travel_multi_agent_planner.agents.transport.TransportAgent = CustomTransportAgent
'''

    def _generate_bike_extension(self, requirement: str) -> str:
        """Generate extension for bicycle preference."""
        _personalization_log.info(f"GENERATING bike extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.transport import TransportAgent

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_day_transport(self, request, daily_spot_plans, current_day):
        _personalization_log.info(f"Applying bike preference for day {{current_day}}")
        result = super().build_day_transport(request, daily_spot_plans, current_day)
        # Add note about biking
        for plan in result:
            if 'transport_notes' not in plan:
                plan['transport_notes'] = []
            plan['transport_notes'].append("【个性化】优先骑行")
        return result

# Monkey patch
import travel_multi_agent_planner.agents.transport
travel_multi_agent_planner.agents.transport.TransportAgent = CustomTransportAgent
'''

    # ============ Hotel Templates ============

    def _generate_near_station_extension(self, requirement: str) -> str:
        """Generate extension for hotel near station preference."""
        _personalization_log.info(f"GENERATING near station hotel extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.hotel import HotelAgent, HotelScoringPolicy

class CustomHotelAgent(HotelAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_hotels(self, request, daily_spot_plans, hotel_options, llm_provider=None, policy=None):
        _personalization_log.info("Applying near-station hotel preference")
        effective_policy = policy or HotelScoringPolicy()
        effective_policy.transit_first = True
        return super().attach_hotels(request, daily_spot_plans, hotel_options, llm_provider, effective_policy)

# Monkey patch
import travel_multi_agent_planner.agents.hotel
travel_multi_agent_planner.agents.hotel.HotelAgent = CustomHotelAgent
'''

    def _generate_luxury_hotel_extension(self, requirement: str) -> str:
        """Generate extension for luxury hotel preference."""
        _personalization_log.info(f"GENERATING luxury hotel extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.hotel import HotelAgent, HotelScoringPolicy

class CustomHotelAgent(HotelAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_hotels(self, request, daily_spot_plans, hotel_options, llm_provider=None, policy=None):
        _personalization_log.info("Applying luxury hotel preference")
        effective_policy = policy or HotelScoringPolicy()
        effective_policy.hotel_budget_override = "premium"
        return super().attach_hotels(request, daily_spot_plans, hotel_options, llm_provider, effective_policy)

# Monkey patch
import travel_multi_agent_planner.agents.hotel
travel_multi_agent_planner.agents.hotel.HotelAgent = CustomHotelAgent
'''

    def _generate_nightlife_hotel_extension(self, requirement: str) -> str:
        """Generate extension for hotel near nightlife area."""
        _personalization_log.info(f"GENERATING nightlife hotel extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.hotel import HotelAgent, HotelScoringPolicy

class CustomHotelAgent(HotelAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_hotels(self, request, daily_spot_plans, hotel_options, llm_provider=None, policy=None):
        _personalization_log.info("Applying nightlife hotel preference")
        effective_policy = policy or HotelScoringPolicy()
        effective_policy.nightlife_first = True
        return super().attach_hotels(request, daily_spot_plans, hotel_options, llm_provider, effective_policy)

# Monkey patch
import travel_multi_agent_planner.agents.hotel
travel_multi_agent_planner.agents.hotel.HotelAgent = CustomHotelAgent
'''

    def _generate_scenic_hotel_extension(self, requirement: str) -> str:
        """Generate extension for hotel near scenic areas."""
        _personalization_log.info(f"GENERATING scenic hotel extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.hotel import HotelAgent, HotelScoringPolicy

class CustomHotelAgent(HotelAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_hotels(self, request, daily_spot_plans, hotel_options, llm_provider=None, policy=None):
        _personalization_log.info("Applying scenic hotel preference")
        effective_policy = policy or HotelScoringPolicy()
        effective_policy.scenic_first = True
        return super().attach_hotels(request, daily_spot_plans, hotel_options, llm_provider, effective_policy)

# Monkey patch
import travel_multi_agent_planner.agents.hotel
travel_multi_agent_planner.agents.hotel.HotelAgent = CustomHotelAgent
'''

    def _generate_quiet_hotel_extension(self, requirement: str) -> str:
        """Generate extension for quiet hotel preference."""
        _personalization_log.info(f"GENERATING quiet hotel extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.hotel import HotelAgent, HotelScoringPolicy

class CustomHotelAgent(HotelAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_hotels(self, request, daily_spot_plans, hotel_options, llm_provider=None, policy=None):
        _personalization_log.info("Applying quiet hotel preference")
        effective_policy = policy or HotelScoringPolicy()
        effective_policy.quiet_first = True
        return super().attach_hotels(request, daily_spot_plans, hotel_options, llm_provider, effective_policy)

# Monkey patch
import travel_multi_agent_planner.agents.hotel
travel_multi_agent_planner.agents.hotel.HotelAgent = CustomHotelAgent
'''

    # ============ Search Templates ============

    def _generate_popular_spots_extension(self, requirement: str) -> str:
        """Generate extension to prioritize popular attractions."""
        _personalization_log.info(f"GENERATING popular spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.search import SearchAgent

class CustomSearchAgent(SearchAgent):
    """Personalized agent for: {requirement[:50]}"""

    def rank_pois(self, pois, request, constraints):
        _personalization_log.info(f"Applying popular spots ranking - {{len(pois)}} POIs")
        # Sort by evidence count descending (popular = more evidence/sources)
        sorted_pois = sorted(
            pois,
            key=lambda p: (-len(getattr(p, 'source_evidence', [])), p.ticket_cost if hasattr(p, 'ticket_cost') else 0)
        )
        _personalization_log.info(f"Popular spots ranking applied: {{len(sorted_pois)}} POIs")
        return sorted_pois

# Monkey patch
import travel_multi_agent_planner.agents.search
travel_multi_agent_planner.agents.search.SearchAgent = CustomSearchAgent
'''

    def _generate_hidden_gems_extension(self, requirement: str) -> str:
        """Generate extension to prioritize hidden gems."""
        _personalization_log.info(f"GENERATING hidden gems extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
import random
from travel_multi_agent_planner.agents.search import SearchAgent

class CustomSearchAgent(SearchAgent):
    """Personalized agent for: {requirement[:50]}"""

    def rank_pois(self, pois, request, constraints):
        _personalization_log.info(f"Applying hidden gems ranking - {{len(pois)}} POIs")
        # Sort by evidence count ascending (hidden = less evidence/sources), shuffle ties
        evidence_sorted = sorted(
            pois,
            key=lambda p: len(getattr(p, 'source_evidence', []))
        )
        # Shuffle top portion to introduce variety among less-evidenced POIs
        split = max(1, len(evidence_sorted) // 3)
        top = evidence_sorted[:split]
        rest = evidence_sorted[split:]
        random.shuffle(top)
        sorted_pois = top + rest
        _personalization_log.info(f"Hidden gems ranking applied: {{len(sorted_pois)}} POIs")
        return sorted_pois

# Monkey patch
import travel_multi_agent_planner.agents.search
travel_multi_agent_planner.agents.search.SearchAgent = CustomSearchAgent
'''

    def _generate_photography_spots_extension(self, requirement: str) -> str:
        """Generate extension to prioritize photogenic spots for photography enthusiasts."""
        _personalization_log.info(f"GENERATING photography spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.search import SearchAgent

class CustomSearchAgent(SearchAgent):
    """Personalized agent for: {requirement[:50]}"""

    def rank_pois(self, pois, request, constraints):
        _personalization_log.info(f"Applying photography ranking - {{len(pois)}} POIs")
        # Prioritize spots with photography-related tags
        def photography_score(poi):
            tag_text = " ".join(getattr(poi, 'tags', []) or []).lower()
            score = len(getattr(poi, 'source_evidence', [])) * 0.5
            if any(w in tag_text for w in ['拍照', '出片', '网红', '美', '日落', '日出', '夜景', '天空', '海', '山']):
                score += 5.0
            if any(w in tag_text for w in ['博物馆', '建筑', '艺术', '展览']):
                score += 2.0
            return score
        sorted_pois = sorted(pois, key=photography_score, reverse=True)
        _personalization_log.info(f"Photography ranking applied: {{len(sorted_pois)}} POIs")
        return sorted_pois

# Monkey patch
import travel_multi_agent_planner.agents.search
travel_multi_agent_planner.agents.search.SearchAgent = CustomSearchAgent
'''

    def _generate_nightlife_spots_extension(self, requirement: str) -> str:
        """Generate extension to prioritize nightlife spots."""
        _personalization_log.info(f"GENERATING nightlife spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.search import SearchAgent

class CustomSearchAgent(SearchAgent):
    """Personalized agent for: {requirement[:50]}"""

    def rank_pois(self, pois, request, constraints):
        _personalization_log.info(f"Applying nightlife ranking - {{len(pois)}} POIs")
        def nightlife_score(poi):
            tag_text = " ".join(getattr(poi, 'tags', []) or []).lower()
            cat_text = getattr(poi, 'category', '').lower()
            score = len(getattr(poi, 'source_evidence', [])) * 0.5
            if any(w in tag_text or w in cat_text for w in ['夜', '夜游', '酒吧', '夜景', '灯光', '娱乐', '夜生活', 'nightlife', 'night', 'bar']):
                score += 5.0
            return score
        sorted_pois = sorted(pois, key=nightlife_score, reverse=True)
        _personalization_log.info(f"Nightlife ranking applied: {{len(sorted_pois)}} POIs")
        return sorted_pois

# Monkey patch
import travel_multi_agent_planner.agents.search
travel_multi_agent_planner.agents.search.SearchAgent = CustomSearchAgent
'''

    def _generate_culture_spots_extension(self, requirement: str) -> str:
        """Generate extension to prioritize cultural spots (museums, history, heritage)."""
        _personalization_log.info(f"GENERATING culture spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.search import SearchAgent

class CustomSearchAgent(SearchAgent):
    """Personalized agent for: {requirement[:50]}"""

    def rank_pois(self, pois, request, constraints):
        _personalization_log.info(f"Applying culture ranking - {{len(pois)}} POIs")
        def culture_score(poi):
            tag_text = " ".join(getattr(poi, 'tags', []) or []).lower()
            cat_text = getattr(poi, 'category', '').lower()
            score = len(getattr(poi, 'source_evidence', [])) * 0.5
            if any(w in tag_text or w in cat_text for w in ['博物馆', '历史', '文化', '人文', '遗产', '古迹', '展览', '艺术', '馆']):
                score += 5.0
            return score
        sorted_pois = sorted(pois, key=culture_score, reverse=True)
        _personalization_log.info(f"Culture ranking applied: {{len(sorted_pois)}} POIs")
        return sorted_pois

# Monkey patch
import travel_multi_agent_planner.agents.search
travel_multi_agent_planner.agents.search.SearchAgent = CustomSearchAgent
'''

    def _generate_food_spots_extension(self, requirement: str) -> str:
        """Generate extension to prioritize food-related spots."""
        _personalization_log.info(f"GENERATING food spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.search import SearchAgent

class CustomSearchAgent(SearchAgent):
    """Personalized agent for: {requirement[:50]}"""

    def rank_pois(self, pois, request, constraints):
        _personalization_log.info(f"Applying food ranking - {{len(pois)}} POIs")
        def food_score(poi):
            tag_text = " ".join(getattr(poi, 'tags', []) or []).lower()
            cat_text = getattr(poi, 'category', '').lower()
            score = len(getattr(poi, 'source_evidence', [])) * 0.5
            if any(w in tag_text or w in cat_text for w in ['美食', '餐饮', '小吃', '夜市', '老街', 'food', '餐厅']):
                score += 5.0
            return score
        sorted_pois = sorted(pois, key=food_score, reverse=True)
        _personalization_log.info(f"Food ranking applied: {{len(sorted_pois)}} POIs")
        return sorted_pois

# Monkey patch
import travel_multi_agent_planner.agents.search
travel_multi_agent_planner.agents.search.SearchAgent = CustomSearchAgent
'''

    def _generate_food_preference_extension(self, requirement: str) -> str:
        """Generate extension for general food preferences."""
        _personalization_log.info(f"GENERATING food preference extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

class CustomFoodSpotAgent(FoodSpotAgent):
    """Personalized agent for: {requirement[:50]}"""

    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        _personalization_log.info(f"Applying general food preference")
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

# Monkey patch
import travel_multi_agent_planner.agents.food_spot
travel_multi_agent_planner.agents.food_spot.FoodSpotAgent = CustomFoodSpotAgent
'''

    # ============ Planner Templates ============

    def _generate_spread_spots_extension(self, requirement: str) -> str:
        """Generate extension to spread spots more evenly."""
        _personalization_log.info(f"GENERATING spread spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, city, day_index, existing_plans):
        _personalization_log.info(f"Applying spread spots preference for day {{day_index}}")
        # Call parent to get normal planning
        result = super().create_daily_spot_plan(request, city, day_index, existing_plans)
        # Reduce visit duration to spread spots more
        if hasattr(result, 'spots'):
            for spot in result.spots:
                if hasattr(spot, 'visit_duration_minutes'):
                    spot.visit_duration_minutes = min(spot.visit_duration_minutes, 90)
            _personalization_log.info(f"Spread spots: adjusted {{len(result.spots)}} spots")
        return result

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _generate_cluster_spots_extension(self, requirement: str) -> str:
        """Generate extension to cluster spots closer together."""
        _personalization_log.info(f"GENERATING cluster spots extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, city, day_index, existing_plans):
        _personalization_log.info(f"Applying cluster spots preference for day {{day_index}}")
        # Call parent - clustering is often the default behavior
        return super().create_daily_spot_plan(request, city, day_index, existing_plans)

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _generate_relaxed_pacing_extension(self, requirement: str) -> str:
        """Generate extension for relaxed pacing (fewer spots per day)."""
        _personalization_log.info(f"GENERATING relaxed pacing extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, city, day_index, existing_plans):
        _personalization_log.info(f"Applying relaxed pacing for day {{day_index}}")
        # Modify request to request fewer spots
        import copy
        modified_request = copy.copy(request)
        # Relaxed pacing: reduce expected spots per day
        result = super().create_daily_spot_plan(modified_request, city, day_index, existing_plans)
        # Reduce visit durations for relaxed feel
        if hasattr(result, 'spots'):
            for spot in result.spots:
                if hasattr(spot, 'visit_duration_minutes'):
                    spot.visit_duration_minutes = min(spot.visit_duration_minutes, 60)
            _personalization_log.info(f"Relaxed pacing: adjusted {{len(result.spots)}} spots")
        return result

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _generate_dense_schedule_extension(self, requirement: str) -> str:
        """Generate extension for dense schedule (more spots per day)."""
        _personalization_log.info(f"GENERATING dense schedule extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, city, day_index, existing_plans):
        _personalization_log.info(f"Applying dense schedule for day {{day_index}}")
        # Increase visit durations for more activities
        result = super().create_daily_spot_plan(request, city, day_index, existing_plans)
        if hasattr(result, 'spots'):
            for spot in result.spots:
                if hasattr(spot, 'visit_duration_minutes'):
                    spot.visit_duration_minutes = min(spot.visit_duration_minutes, 150)
            _personalization_log.info(f"Dense schedule: adjusted {{len(result.spots)}} spots")
        return result

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    def _generate_planner_extension(self, requirement: str) -> str:
        """Generate generic planner extension."""
        _personalization_log.info(f"GENERATING planner extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    """Personalized agent for: {requirement[:50]}"""

    def create_daily_spot_plan(self, request, city, day_index, existing_plans):
        _personalization_log.info(f"Applying planner customization for day {{day_index}}")
        return super().create_daily_spot_plan(request, city, day_index, existing_plans)

# Monkey patch
import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
'''

    # ============ Transport Templates ============

    def _generate_car_mode_extension(self, requirement: str) -> str:
        """Generate extension for car/driving mode."""
        _personalization_log.info(f"GENERATING car mode extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.transport import TransportAgent

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_day_transport(self, request, daily_spot_plans, current_day):
        _personalization_log.info(f"Applying car mode preference for day {{current_day}}")
        return super().build_day_transport(request, daily_spot_plans, current_day)

# Monkey patch
import travel_multi_agent_planner.agents.transport
travel_multi_agent_planner.agents.transport.TransportAgent = CustomTransportAgent
'''

    def _generate_parking_extension(self, requirement: str) -> str:
        """Generate extension for parking needs."""
        _personalization_log.info(f"GENERATING parking extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.transport import TransportAgent

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_day_transport(self, request, daily_spot_plans, current_day):
        _personalization_log.info(f"Applying parking consideration for day {{current_day}}")
        return super().build_day_transport(request, daily_spot_plans, current_day)

# Monkey patch
import travel_multi_agent_planner.agents.transport
travel_multi_agent_planner.agents.transport.TransportAgent = CustomTransportAgent
'''

    def _generate_transport_extension(self, requirement: str) -> str:
        """Generate generic transport extension."""
        _personalization_log.info(f"GENERATING transport extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.transport import TransportAgent

class CustomTransportAgent(TransportAgent):
    """Personalized agent for: {requirement[:50]}"""

    def build_day_transport(self, request, daily_spot_plans, current_day):
        _personalization_log.info(f"Applying transport customization for day {{current_day}}")
        return super().build_day_transport(request, daily_spot_plans, current_day)

# Monkey patch
import travel_multi_agent_planner.agents.transport
travel_multi_agent_planner.agents.transport.TransportAgent = CustomTransportAgent
'''

    # ============ Budget Templates ============

    def _generate_budget_extension(self, requirement: str, style: str) -> str:
        """Generate extension for budget style preference."""
        _personalization_log.info(f"GENERATING budget ({style}) extension for: {requirement[:50]}")
        return f'''from __future__ import annotations
from travel_multi_agent_planner.agents.budget import BudgetAgent

class CustomBudgetAgent(BudgetAgent):
    """Personalized agent for: {requirement[:50]} (style: {style})"""

    def build_budget(self, request, profile, day_plans, round_trip_transport_cost, round_trip_note):
        _personalization_log.info(f"Applying {{style}} budget preference")
        import copy
        modified_request = copy.copy(request)
        modified_request.food_budget_preference = "{style}"
        modified_request.hotel_budget_preference = "{style}"
        result = super().build_budget(modified_request, profile, day_plans, round_trip_transport_cost, round_trip_note)
        _personalization_log.info(f"Budget calculation completed with {{style}} style")
        return result

# Monkey patch
import travel_multi_agent_planner.agents.budget
travel_multi_agent_planner.agents.budget.BudgetAgent = CustomBudgetAgent
'''

    def _fix_imports(self, code: str) -> str:
        """Fix common import mistakes in LLM-generated code."""
        if code and "from __future__ import annotations" not in code:
            code = "from __future__ import annotations\n" + code

        # Fix: import XxxAgent -> from travel_multi_agent_planner.agents.xxx import XxxAgent
        import_match = re.search(r'^import\s+(\w+Agent)', code, re.MULTILINE)
        if import_match:
            agent_name = import_match.group(1)
            if agent_name in self.AGENT_MODULE_MAP:
                code = re.sub(
                    r'^import\s+' + agent_name,
                    f'from {self.AGENT_MODULE_MAP[agent_name]} import {agent_name}',
                    code,
                    flags=re.MULTILINE
                )

        # Fix: from agents.xxx import XxxAgent -> from travel_multi_agent_planner.agents.xxx import XxxAgent
        code = re.sub(
            r'from\s+agents\.(\w+)\s+import\s+(\w+Agent)',
            r'from travel_multi_agent_planner.agents.\1 import \2',
            code
        )

        return code

    def _get_agent_context(self) -> str:
        """
        Get comprehensive context about available agents and their complete method signatures.
        """
        return """## Available Agents and Their Methods

### 1. FoodSpotAgent (travel_multi_agent_planner.agents.food_spot)
Handles meal (breakfast/lunch/dinner) assignment for travel plans.

Key Method:
```python
def attach_meals(
    self,
    request: TripRequest,
    daily_spot_plans: list[dict],
    food_options: list[FoodVenue] | None = None,
    used_food_keys: set[str] | None = None,
) -> list[dict]:
    # Assigns meals to each day's plan
    # Each plan in daily_spot_plans gets a 'meals' key with list of MealRecommendation
    # Returns the modified daily_spot_plans
```

Helper Methods (you can use these in your extension):
```python
def _pick_food(self, meal_type, candidates, request, used_keys, day_used_keys,
               day_index, anchor, route_path, allow_repeat) -> tuple[MealRecommendation, str]
def _food_score(self, food, request, meal_type, used_keys, anchor,
                route_distance, anchor_distance) -> tuple[float, float, float, float, float]
def _cost_for_meal(self, food: FoodVenue, meal_type: str, day_index: int) -> float
def _build_reason(self, food, meal_type, interests, tastes) -> str
```

### 2. TransportAgent (travel_multi_agent_planner.agents.transport)
Handles transportation planning between attractions.

Key Method:
```python
def build_day_transport(
    self,
    request: TripRequest,
    daily_spot_plans: list[dict],
    current_day: int,
) -> list[dict]:
    # Plans transport segments between spots for the day
    # Returns updated daily_spot_plans
```

### 3. PlannerAgent (travel_multi_agent_planner.agents.planner)
Creates daily spot visit plans.

Key Method:
```python
def create_daily_spot_plan(
    self,
    request: TripRequest,
    city: str,
    day_index: int,
    existing_plans: list[dict],
) -> dict:
    # Creates single-day attraction plan
    # Returns plan dict with 'spots', 'notes', etc.
```

### 4. HotelAgent (travel_multi_agent_planner.agents.hotel)
Selects appropriate hotels.

Key Method:
```python
def select_hotel(
    self,
    request: TripRequest,
    city: str,
    district: str | None = None,
) -> dict:
    # Selects hotel based on budget, location preferences
    # Returns hotel info dict
```

### 5. BudgetAgent (travel_multi_agent_planner.agents.budget)
Calculates trip budget.

Key Method:
```python
def calculate_budget(
    self,
    request: TripRequest,
    daily_plans: list[dict],
) -> dict:
    # Calculates total budget based on hotels, meals, transport
    # Returns budget info dict
```

### 6. SearchAgent (travel_multi_agent_planner.agents.search)
Searches and ranks POIs (points of interest).

Key Methods:
```python
def build_city_profile(self, request: TripRequest, search_provider: object) -> CityProfile
def ensure_required_spots(self, profile: CityProfile, constraints: TravelConstraints) -> list[PointOfInterest]
def rank_pois(self, pois: list[PointOfInterest], request: TripRequest) -> list[PointOfInterest]
```

### 7. ConstraintValidatorAgent (travel_multi_agent_planner.agents.validator)
Validates travel plans against constraints.

Key Method:
```python
def validate(
    self,
    request: TripRequest,
    constraints: TravelConstraints,
    daily_plans: list[dict],
    city_profile: CityProfile,
) -> tuple[list[ValidationIssue], float]:
    # Validates plan and returns (issues, score)
```

## Data Models (CRITICAL - Use correct field names!)

```python
@dataclass
class FoodVenue:
    name: str           # Restaurant name (NOT 'venue_name')
    district: str       # District/location
    cuisine: str        # Cuisine type
    description: str    # Description
    average_cost: float # Average cost
    tags: list[str]     # Tags like ['火锅', '川菜', '辣']
    taste_profile: list[str]  # Taste preferences
    recommended_meal: str = "flexible"
    meal_suitability: str = "both"  # "lunch", "dinner", or "both"
    lat: float = 0.0
    lon: float = 0.0
    address: str = ""
    source_evidence: list[EvidenceItem]

@dataclass
class MealRecommendation:
    venue_name: str     # String name, NOT FoodVenue object!
    meal_type: str      # "lunch" or "dinner" (NOT "breakfast"!)
    estimated_cost: float
    reason: str
    venue_district: str = ""
    cuisine: str = ""
    lat: float = 0.0
    lon: float = 0.0
    anchor_distance_km: float = 0.0
    route_distance_km: float = 0.0
    fallback_used: bool = False
    selection_tier: str = "strict"
    source_evidence: list[EvidenceItem] = field(default_factory=list)
```

## Common Override Patterns

### Pattern 1: Filter food options (BEST for food preferences)
```python
class CustomFoodSpotAgent(FoodSpotAgent):
    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        if food_options:
            # Filter to prioritize user's preference
            preferred = [f for f in food_options if '火锅' in f.name]
            if preferred:
                food_options = preferred + [f for f in food_options if f not in preferred]
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)
```

### Pattern 2: Skip a meal type
```python
class CustomFoodSpotAgent(FoodSpotAgent):
    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        result = super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)
        for plan in result:
            if 'meals' in plan:
                plan['meals'] = [m for m in plan['meals'] if m.meal_type != "lunch"]
        return result
```

### Pattern 3: Modify transport behavior
```python
class CustomTransportAgent(TransportAgent):
    def build_day_transport(self, request, daily_spot_plans, current_day):
        # Your modification
        return super().build_day_transport(request, daily_spot_plans, current_day)
```

## CRITICAL RULES
1. MealRecommendation uses venue_name (STRING), NOT a FoodVenue object
2. meal_type must be "lunch" or "dinner" - NOT "breakfast"
3. ALWAYS call super().method() unless you have a very specific reason not to
4. If you try to manually create MealRecommendation objects, you're probably doing it wrong - use super() instead
"""

    def _detect_target(self, requirement_text: str) -> tuple[str, str, list[str]]:
        """
        Detect which agent/method to target based on requirement text.
        """
        text_lower = requirement_text.lower()

        # Food-related requirements
        if any(word in text_lower for word in ["吃", "餐", "饭", "早餐", "午饭", "午餐", "晚饭", "晚餐", "火锅", "美食", "餐厅", "酒吧", "餐饮", "food", "eat"]):
            return ("food_spot", "attach_meals", ["food", "meal", "restaurant"])

        # Transport-related
        if any(word in text_lower for word in ["开车", "停车", "车", "驾驶", "交通", "路线", "路途", "自驾", "transport", "drive"]):
            return ("transport", "build_day_transport", ["transport", "route", "drive"])

        # Hotel-related
        if any(word in text_lower for word in ["酒店", "住宿", "旅馆", "民宿", "宾馆", "hotel"]):
            return ("hotel", "select_hotel", ["hotel", "accommodation"])

        # Planning-related
        if any(word in text_lower for word in ["景点", "行程", "规划", "安排", "分散", "集中", "每天", "观光", "spot", "plan"]):
            return ("planner", "create_daily_spot_plan", ["spots", "plan", "schedule"])

        # Budget-related
        if any(word in text_lower for word in ["预算", "省钱", "费用", "花钱", "奢侈", "高端", "经济", "budget", "cost"]):
            return ("budget", "calculate_budget", ["budget", "cost", "expense"])

        # Search/rank related
        if any(word in text_lower for word in ["搜索", "查找", "排名", "优先", "rank", "search"]):
            return ("search", "rank_pois", ["search", "rank", "poi"])

        # Validation related
        if any(word in text_lower for word in ["校验", "验证", "检查", "constraint", "validate"]):
            return ("validator", "validate", ["validate", "constraint"])

        # Default to food_spot
        return ("food_spot", "attach_meals", ["general"])

    def _compute_diff(self, old_content: str, new_content: str) -> list[str]:
        """Compute unified diff between old and new content."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        return diff

    def _get_agent_module_path(self, agent_name: str) -> tuple[str, str]:
        """Get the module path and class name for an agent."""
        if agent_name in RUNTIME_AGENT_SPECS:
            return RUNTIME_AGENT_SPECS[agent_name]
        if agent_name in self.AGENT_MODULE_MAP:
            return (self.AGENT_MODULE_MAP[agent_name], f"{agent_name.title()}Agent")
        return (f"travel_multi_agent_planner.agents.{agent_name}", f"{agent_name.title()}Agent")

    def _save_original_class(self, agent_name: str) -> bool:
        """Save the original class for an agent before patching."""
        if agent_name in self._original_agent_classes:
            return True

        try:
            module_path, class_name = self._get_agent_module_path(agent_name)
            import importlib
            module = importlib.import_module(module_path)
            original_class = getattr(module, class_name, None)
            if original_class is not None:
                self._original_agent_classes[agent_name] = original_class
                print(f"[CodeModifier] Saved original {class_name} for agent {agent_name}")
                return True
        except Exception as e:
            print(f"[CodeModifier] Failed to save original class for {agent_name}: {e}")
        return False

    def _restore_original_class(self, agent_name: str) -> bool:
        """Restore the original class for an agent."""
        if agent_name not in self._original_agent_classes and agent_name in ORIGINAL_RUNTIME_AGENT_CLASSES:
            self._original_agent_classes[agent_name] = ORIGINAL_RUNTIME_AGENT_CLASSES[agent_name]
        if agent_name not in self._original_agent_classes:
            self._save_original_class(agent_name)
            if agent_name not in self._original_agent_classes:
                return False

        try:
            module_path, class_name = self._get_agent_module_path(agent_name)
            import importlib
            module = importlib.import_module(module_path)
            original_class = self._original_agent_classes[agent_name]
            setattr(module, class_name, original_class)
            print(f"[CodeModifier] Restored original {class_name} for agent {agent_name}")
            return True
        except Exception as e:
            print(f"[CodeModifier] Failed to restore original class for {agent_name}: {e}")
        return False

    def _unload_extensions_for_agent(self, agent_name: str) -> list[str]:
        """Unload all extensions for a given agent before applying a new one."""
        unloaded = []

        if agent_name not in self._loaded_extensions:
            return unloaded

        self._restore_original_class(agent_name)
        sync_runtime_agent_bindings(agent_name)
        self._refresh_orchestrator_instances(agent_name)
        unloaded = list(self._loaded_extensions.get(agent_name, set()))
        self._loaded_extensions[agent_name] = set()

        print(f"[CodeModifier] Unloaded {len(unloaded)} extension(s) for agent {agent_name}: {unloaded}")
        return unloaded

    def _register_extension(self, extension_path: str, target_agent: str) -> None:
        """Register an extension as loaded for the target agent."""
        if target_agent not in self._loaded_extensions:
            self._loaded_extensions[target_agent] = set()

        filename = extension_path.split("/")[-1].split("\\")[-1]
        self._loaded_extensions[target_agent].add(filename)

    def _refresh_orchestrator_instances(self, target_agent: str) -> None:
        """
        Refresh agent instances in all orchestrators after applying a new extension.
        This recreates the agent instance so the monkey-patched class takes effect.
        """
        import gc
        from travel_multi_agent_planner.orchestrator import TravelPlanningOrchestrator

        AGENT_MAP = {
            "food_spot": "food_spot",
            "transport": "transport",
            "planner": "planner",
            "hotel": "hotel_agent",
            "budget": "budget",
            "search": "search_agent",
            "requirement": "requirement",
        }

        if target_agent not in AGENT_MAP:
            return

        attr_name = AGENT_MAP[target_agent]
        module_path, class_name = self._get_agent_module_path(target_agent)

        for obj in gc.get_objects():
            if isinstance(obj, TravelPlanningOrchestrator):
                try:
                    old_agent = getattr(obj, attr_name, None)
                    module = importlib.import_module(module_path)
                    agent_class = getattr(module, class_name, None)
                    if old_agent is not None and agent_class is not None:
                        if target_agent == "transport":
                            new_agent = agent_class(
                                intercity_provider=getattr(old_agent, "intercity_provider", None),
                                llm_provider=getattr(old_agent, "llm_provider", None),
                            )
                        else:
                            new_agent = agent_class()
                        setattr(obj, attr_name, new_agent)
                except Exception:
                    pass

    def apply(self, patch: ModificationPatch, base_path: Path, save_extensions: bool = True, parsed_requirement=None) -> dict[str, Any]:
        """Apply a modification patch to the filesystem."""
        applied_files = []
        imported_extensions = []
        errors = []

        # Create extensions directory
        extensions_dir = base_path / "personalization" / "extensions"
        extensions_dir.mkdir(parents=True, exist_ok=True)

        # Create __init__.py if needed
        init_file = extensions_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text(
                '"""Auto-generated extension modules.\n\n'
                'This directory contains extension modules that modify system behavior\n'
                'at runtime via monkey patching. Each file is auto-generated and should\n'
                'not be edited manually.\n'
                '"""\n',
                encoding="utf-8",
            )

        for file_patch in patch.patches:
            try:
                target = base_path / file_patch.file_path

                metadata = file_patch.metadata if isinstance(file_patch.metadata, dict) else {}
                target_agent = metadata.get("target_agent")

                if file_patch.operation == PatchOperation.CREATE:
                    if target_agent:
                        self._save_original_class(target_agent)
                        self._unload_extensions_for_agent(target_agent)

                    target.parent.mkdir(parents=True, exist_ok=True)

                    if save_extensions:
                        target.write_text(file_patch.new_snippet, encoding="utf-8")
                        applied_files.append(file_patch.file_path)
                    else:
                        applied_files.append(f"[memory] {file_patch.file_path}")

                    extension_module = self._import_extension(file_patch.file_path, base_path, file_patch.new_snippet, keep_file=save_extensions)
                    if extension_module is None:
                        if target_agent:
                            self._restore_original_class(target_agent)
                            sync_runtime_agent_bindings(target_agent)
                            self._refresh_orchestrator_instances(target_agent)
                        errors.append(f"Failed to import extension {file_patch.file_path}")
                        continue

                    imported_extensions.append(file_patch.file_path)
                    ensure_extension_runtime(extension_module)
                    if target_agent:
                        self._register_extension(file_patch.file_path, target_agent)
                        sync_runtime_agent_bindings(target_agent)
                        self._refresh_orchestrator_instances(target_agent)

                elif file_patch.operation == PatchOperation.MODIFY:
                    if target.exists():
                        content = target.read_text(encoding="utf-8")
                        new_content = self._apply_patch_to_content(content, file_patch)
                        target.write_text(new_content, encoding="utf-8")
                        applied_files.append(file_patch.file_path)

                elif file_patch.operation == PatchOperation.DELETE:
                    if target.exists():
                        target.unlink()
                        applied_files.append(file_patch.file_path)

            except Exception as e:
                errors.append(f"Failed to apply {file_patch.file_path}: {str(e)}")

        return {
            "success": len(errors) == 0,
            "applied_files": applied_files,
            "imported_extensions": imported_extensions,
            "errors": errors,
        }

    def _import_extension(self, extension_path: str, base_path: Path, snippet: str = "", keep_file: bool = True) -> Any:
        """Dynamically import an extension module to apply its monkey patch."""
        try:
            module_path = extension_path.replace("/", ".").replace("\\", ".")
            if module_path.endswith(".py"):
                module_path = module_path[:-3]

            file_path = base_path / extension_path
            file_existed = file_path.exists()
            if not file_existed and snippet:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(snippet, encoding="utf-8")
                importlib.invalidate_caches()

            base_str = str(base_path)
            if base_str not in sys.path:
                sys.path.insert(0, base_str)

            if module_path in sys.modules:
                del sys.modules[module_path]
            importlib.invalidate_caches()
            module = importlib.import_module(module_path)
            ensure_extension_runtime(module)

            if not file_existed and snippet and not keep_file:
                try:
                    file_path.unlink()
                except Exception:
                    pass

            return module

        except Exception as e:
            print(f"Warning: Failed to import extension {extension_path}: {e}")
            return None

    def _apply_patch_to_content(self, content: str, file_patch: FilePatch) -> str:
        """Apply a file patch to existing content."""
        if not file_patch.original_snippet:
            return self._apply_diff_lines(content, file_patch.diff_lines)

        return content.replace(file_patch.original_snippet, file_patch.new_snippet)

    def _apply_diff_lines(self, content: str, diff_lines: list[str]) -> str:
        """Apply a diff to content."""
        if not diff_lines:
            return content

        lines = content.splitlines()
        result_lines = []
        idx = 0

        for line in diff_lines:
            if line.startswith("@@"):
                match = re.search(r"@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@", line)
                if match:
                    old_start = int(match.group(1))
                    while idx < old_start - 1 and idx < len(lines):
                        result_lines.append(lines[idx])
                        idx += 1
            elif line.startswith("-"):
                idx += 1
            elif line.startswith("+"):
                result_lines.append(line[1:])
            elif line.startswith(" "):
                if idx < len(lines):
                    result_lines.append(lines[idx])
                    idx += 1

        while idx < len(lines):
            result_lines.append(lines[idx])
            idx += 1

        return "\n".join(result_lines)

    def save_patch(self, patch: ModificationPatch, patches_dir: Path) -> str:
        """Save a patch to disk for later use."""
        patches_dir.mkdir(parents=True, exist_ok=True)
        patch_file = patches_dir / f"{patch.patch_id}.json"
        patch_file.write_text(json.dumps(patch.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(patch_file)

    def load_patch(self, patch_id: str, patches_dir: Path) -> ModificationPatch | None:
        """Load a saved patch."""
        patch_file = patches_dir / f"{patch_id}.json"
        if not patch_file.exists():
            return None

        data = json.loads(patch_file.read_text(encoding="utf-8"))
        patches = [FilePatch(**p) for p in data.get("patches", [])]
        return ModificationPatch(
            patches=patches,
            requirement_id=data.get("requirement_id", ""),
            patch_id=data.get("patch_id", patch_id),
        )

# RELOAD MARKER 4651956665916235909
