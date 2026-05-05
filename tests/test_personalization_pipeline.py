from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from personalization.agents.code_modifier import CodeModifierAgent
from personalization.agents.code_generator import CodeGeneratorAgent
from personalization.agents.code_validator import CodeValidatorAgent
from personalization.engine import PersonalizationEngine
from personalization.models import (
    FilePatch,
    ModificationPatch,
    ModificationType,
    ParsedRequirement,
    PatchMetadata,
    PatchOperation,
    PersonalizationResult,
)
from travel_multi_agent_planner.providers.bailian import BailianLLMProvider


REPO_ROOT = Path(__file__).resolve().parents[1]


def make_patch(target_agent: str, target_method: str, code: str, requirement_id: str = "test-req") -> ModificationPatch:
    return ModificationPatch(
        patches=[
            FilePatch(
                file_path="personalization/extensions/test_extension.py",
                operation=PatchOperation.CREATE,
                new_snippet=code,
                metadata={
                    "target_agent": target_agent,
                    "target_method": target_method,
                    "generated_by": "test",
                },
            )
        ],
        metadata=PatchMetadata(description="test patch"),
        requirement_id=requirement_id,
    )


class TestBailianProvider(unittest.TestCase):
    def test_unified_provider_methods_delegate(self) -> None:
        provider = BailianLLMProvider(api_key="dummy")
        provider._chat_text = lambda system_prompt, user_prompt, temperature=0.3: f"{system_prompt}|{user_prompt}|{temperature}"  # type: ignore[method-assign]
        provider._chat_json = lambda system_prompt, user_prompt, temperature=0.1: {  # type: ignore[method-assign]
            "system": system_prompt,
            "payload": user_prompt,
            "temperature": temperature,
        }

        self.assertIn("sys|user|0.2", provider.generate_text("sys", "user", temperature=0.2) or "")
        json_result = provider.generate_json("sys", {"x": 1}, schema_hint="{}", temperature=0.05)
        self.assertIsInstance(json_result, dict)
        self.assertEqual(json_result["system"], "sys")
        self.assertIn('"x": 1', json_result["payload"])
        self.assertEqual(provider.generate_code("sys", {"x": 2}), 'sys|{"x": 2}|0.2')
        self.assertEqual(provider.generate_repair("sys", {"x": 3}), 'sys|{"x": 3}|0.15')


class TestPersonalizationPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = CodeValidatorAgent()
        self.modifier = CodeModifierAgent()

    def test_code_generator_prefers_controlled_template_for_known_agents(self) -> None:
        class StubLLM:
            def generate_code(self, system_prompt, user_payload, temperature=0.2):
                return """
from __future__ import annotations
from travel_multi_agent_planner.agents.food_spot import FoodSpotAgent

class CustomFoodSpotAgent(FoodSpotAgent):
    def attach_meals(self, request, daily_spot_plans, food_options=None, used_food_keys=None):
        return super().attach_meals(request, daily_spot_plans, food_options, used_food_keys)

import travel_multi_agent_planner.agents.food_spot as m
m.FoodSpotAgent = CustomFoodSpotAgent
""".strip()

        generator = CodeGeneratorAgent(StubLLM())
        requirement = ParsedRequirement(
            raw_text="第一天晚上吃火锅",
            target_files=["travel_multi_agent_planner/agents/food_spot.py"],
            modification_type=ModificationType.CODE,
            parameters={},
            requirement_id="llm-food-req",
        )
        task = {"id": "sub_1", "text": "第一天晚上吃火锅", "scope": {"days": [1], "meals": ["dinner"]}}
        resolution = {"target_agent": "food_spot", "target_method": "attach_meals"}
        code_plan = {"target_agent": "food_spot", "target_method": "attach_meals"}

        patch_obj, source = generator.generate(requirement, task, resolution, code_plan, REPO_ROOT)
        self.assertEqual(source, "template")
        self.assertTrue(patch_obj.patches)
        self.assertIn("CustomFoodSpotAgent", patch_obj.patches[0].new_snippet)

    def test_code_generator_extracts_natural_language_meal_slots(self) -> None:
        generator = CodeGeneratorAgent()
        self.assertEqual(generator._extract_meal_type({"text": "第二天晚上吃日料"}), "dinner")
        self.assertEqual(generator._extract_meal_type({"text": "第三天中午吃面"}), "lunch")

    def test_code_generator_has_single_method_implementations(self) -> None:
        source = (REPO_ROOT / "personalization" / "agents" / "code_generator.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("def _build_food_rules"), 1)
        self.assertEqual(source.count("def _extract_meal_type"), 1)
        self.assertEqual(source.count("def _is_valid_extension"), 1)
        self.assertNotIn("CodeGeneratorAgent._build_food_rules =", source)

    def test_requirement_parser_and_splitter_cover_food_and_transport(self) -> None:
        from personalization.agents.requirement_parser import RequirementParserAgent
        from personalization.agents.requirement_splitter import RequirementSplitterAgent
        from personalization.agents.target_resolver import TargetResolverAgent

        parser = RequirementParserAgent()
        splitter = RequirementSplitterAgent()
        resolver = TargetResolverAgent()
        parsed = parser.parse("第一天晚上吃火锅，第二天晚上吃日料，第三天中午吃面，第一天出发不要太早")
        tasks = splitter.split(parsed.raw_text, parsed.parameters)
        agents = [resolver.resolve(task, parsed.parameters)["target_agent"] for task in tasks]
        self.assertIn("food_spot", agents)
        self.assertIn("transport", agents)
        self.assertNotIn("planner", agents)

    def test_clear_endpoint_import_is_valid(self) -> None:
        from personalization.engine import PersonalizationEngine

        with patch.object(PersonalizationEngine, "_load_saved_extensions", return_value=None):
            engine = PersonalizationEngine(REPO_ROOT, llm_provider=None)
        result = engine.clear_all_extensions()
        self.assertIn("extensions_cleared", result)

    def test_personalization_router_reports_uninitialized_engine(self) -> None:
        from backend.routers import personalization

        with patch("backend.main._personalization_engine", None):
            with self.assertRaises(HTTPException) as caught:
                personalization._get_engine()
        self.assertEqual(caught.exception.status_code, 503)

    def test_runtime_signature_validator_blocks_bad_planner_extension(self) -> None:
        bad_code = """
from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    def create_daily_spot_plan(self, request):
        return super().create_daily_spot_plan(request)

import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
""".strip()
        patch = make_patch("planner", "create_daily_spot_plan", bad_code)
        result = self.validator.validate(patch, REPO_ROOT)
        self.assertFalse(result.success)
        self.assertFalse(result.runtime_signature_ok)

    def test_process_requirement_returns_new_pipeline_fields(self) -> None:
        with patch.object(PersonalizationEngine, "_load_saved_extensions", return_value=None):
            engine = PersonalizationEngine(REPO_ROOT, llm_provider=None)
        result = asyncio.run(engine.process_requirement("行程轻松一点"))
        self.assertIsInstance(result, PersonalizationResult)
        self.assertTrue(result.agent_trace)
        self.assertTrue(result.sub_requirements)
        self.assertGreaterEqual(result.attempt_count, 1)
        self.assertIn(result.final_generation_source, {"template", "llm", "repaired"})
        self.assertIn("parser", result.stage_statuses)
        self.assertIn("validator", result.stage_statuses)

    def test_repair_loop_marks_result_as_repaired(self) -> None:
        with patch.object(PersonalizationEngine, "_load_saved_extensions", return_value=None):
            engine = PersonalizationEngine(REPO_ROOT, llm_provider=None)

        requirement = ParsedRequirement(
            raw_text="行程轻松一点",
            target_files=["travel_multi_agent_planner/agents/planner.py"],
            modification_type=ModificationType.CODE,
            parameters={},
            requirement_id="repair-req",
        )
        task = {
            "id": "sub_1",
            "text": "行程轻松一点",
            "scope": {"days": [], "meals": []},
            "dependency": "independent",
            "source": "test",
        }
        resolution = {
            "target_agent": "planner",
            "target_method": "create_daily_spot_plan",
            "change_strategy": "runtime_extension",
            "source": "test",
        }
        code_plan = {
            "target_agent": "planner",
            "target_method": "create_daily_spot_plan",
            "change_strategy": "runtime_extension",
            "expected_behavior": "行程轻松一点",
            "patch_style": "override_and_super",
            "acceptance_checks": ["signature"],
        }

        bad_code = """
from __future__ import annotations
from travel_multi_agent_planner.agents.planner import PlannerAgent

class CustomPlannerAgent(PlannerAgent):
    def create_daily_spot_plan(self, request):
        return super().create_daily_spot_plan(request)

import travel_multi_agent_planner.agents.planner
travel_multi_agent_planner.agents.planner.PlannerAgent = CustomPlannerAgent
""".strip()
        good_code = self.modifier._build_planner_runtime_extension("行程轻松一点")
        bad_patch = make_patch("planner", "create_daily_spot_plan", bad_code, requirement_id="repair-req")
        good_patch = make_patch("planner", "create_daily_spot_plan", good_code, requirement_id="repair-req")

        engine.requirement_parser.parse = lambda user_text, context=None: requirement  # type: ignore[method-assign]
        engine.requirement_splitter.split = lambda user_text, parsed_parameters=None: [task]  # type: ignore[method-assign]
        engine.target_resolver.resolve = lambda task_item, parsed_parameters=None: resolution  # type: ignore[method-assign]
        engine.code_planner.plan = lambda task_item, resolution_item, parsed_parameters=None: code_plan  # type: ignore[method-assign]
        engine.code_generator.generate = lambda **kwargs: (bad_patch, "llm")  # type: ignore[method-assign]

        repair_calls = {"count": 0}

        def repair_once(**kwargs):
            if repair_calls["count"] == 0:
                repair_calls["count"] += 1
                return good_patch
            return None

        engine.code_fixer.repair = repair_once  # type: ignore[method-assign]

        result = asyncio.run(engine.process_requirement("行程轻松一点"))
        self.assertEqual(result.repair_attempts, 1)
        self.assertEqual(result.final_generation_source, "repaired")
        self.assertIn(result.status, {"pending_approval", "pending_review"})

    def test_apply_blocks_when_result_has_blocking_issues(self) -> None:
        with patch.object(PersonalizationEngine, "_load_saved_extensions", return_value=None):
            engine = PersonalizationEngine(REPO_ROOT, llm_provider=None)

        requirement = ParsedRequirement(
            raw_text="酒店靠近地铁",
            target_files=["travel_multi_agent_planner/agents/hotel.py"],
            modification_type=ModificationType.CODE,
            parameters={},
            requirement_id="blocked-req",
        )
        code = self.modifier._build_hotel_runtime_extension("酒店靠近地铁")
        patch_obj = make_patch("hotel", "attach_hotels", code, requirement_id="blocked-req")
        engine._pending_results["blocked-req"] = PersonalizationResult(
            parsed_requirement=requirement,
            modification_patch=patch_obj,
            blocking_issues=["sub_1: validation:runtime_signature_mismatch"],
            status="pending_review",
        )

        apply_result = asyncio.run(engine.apply_modification("blocked-req", approved=True))
        self.assertFalse(apply_result.success)
        self.assertEqual(apply_result.status, "blocked")


if __name__ == "__main__":
    unittest.main()
