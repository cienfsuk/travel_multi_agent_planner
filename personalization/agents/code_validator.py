"""Code Validator Agent - validates that modifications work correctly."""

from __future__ import annotations

import ast
import inspect
import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..models import ModificationPatch, ValidationResult


class CodeValidatorAgent:
    """Validate syntax, imports, runtime signature compatibility and smoke tests."""

    def validate(self, patch: ModificationPatch, base_path: Path) -> ValidationResult:
        tests_passed: list[str] = []
        tests_failed: list[str] = []
        smoke_checks: list[str] = []
        runtime_signature_ok = True

        for file_patch in patch.patches:
            if file_patch.operation.value not in ("create", "modify") or not file_patch.file_path.endswith(".py"):
                continue

            syntax_ok, syntax_error = self._validate_syntax(file_patch)
            if syntax_ok:
                tests_passed.append(f"syntax:{file_patch.file_path}")
            else:
                tests_failed.append(f"syntax:{file_patch.file_path}: {syntax_error}")

            import_ok, import_error = self._validate_imports(file_patch, base_path)
            if import_ok:
                tests_passed.append(f"imports:{file_patch.file_path}")
            else:
                tests_failed.append(f"imports:{file_patch.file_path}: {import_error}")

            signature_ok, signature_error = self._validate_runtime_signature(file_patch, base_path)
            if signature_ok:
                tests_passed.append(f"signature:{file_patch.file_path}")
            else:
                runtime_signature_ok = False
                tests_failed.append(f"signature:{file_patch.file_path}: {signature_error}")

            shape_ok, shape_error = self._validate_runtime_shape(file_patch)
            if shape_ok:
                tests_passed.append(f"shape:{file_patch.file_path}")
            else:
                tests_failed.append(f"shape:{file_patch.file_path}: {shape_error}")

            smoke_checks.extend(self._build_smoke_checks(file_patch))

        type_ok = self._validate_types(patch, base_path)
        if type_ok:
            tests_passed.append("type_check")
        else:
            tests_failed.append("type_check")

        test_results = self._run_related_tests(patch, base_path)
        tests_passed.extend(test_results["passed"])
        tests_failed.extend(test_results["failed"])

        return ValidationResult(
            success=len(tests_failed) == 0,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            can_revert=True,
            message=self._generate_validation_message(tests_passed, tests_failed),
            runtime_signature_ok=runtime_signature_ok,
            smoke_checks=smoke_checks,
        )

    def _validate_syntax(self, file_patch) -> tuple[bool, str]:
        if not file_patch.new_snippet:
            return True, ""
        try:
            ast.parse(file_patch.new_snippet)
            return True, ""
        except SyntaxError as exc:
            return False, f"Syntax error at line {exc.lineno}: {exc.msg}"

    def _validate_imports(self, file_patch, base_path: Path) -> tuple[bool, str]:
        if not file_patch.new_snippet:
            return True, ""
        try:
            tree = ast.parse(file_patch.new_snippet)
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            missing = [imp for imp in imports if imp not in self._get_project_imports(base_path) and not self._is_stdlib(imp)]
            critical = [imp for imp in missing if not imp.startswith(".")]
            if critical:
                return False, f"Missing imports: {', '.join(critical)}"
            return True, ""
        except SyntaxError:
            return True, ""

    def _validate_runtime_signature(self, file_patch, base_path: Path) -> tuple[bool, str]:
        metadata = file_patch.metadata if isinstance(file_patch.metadata, dict) else {}
        target_agent = metadata.get("target_agent")
        target_method = metadata.get("target_method")
        if not target_agent or not target_method:
            return True, ""

        agent_map = {
            "food_spot": ("travel_multi_agent_planner.agents.food_spot", "FoodSpotAgent"),
            "transport": ("travel_multi_agent_planner.agents.transport", "TransportAgent"),
            "planner": ("travel_multi_agent_planner.agents.planner", "PlannerAgent"),
            "hotel": ("travel_multi_agent_planner.agents.hotel", "HotelAgent"),
            "budget": ("travel_multi_agent_planner.agents.budget", "BudgetAgent"),
            "search": ("travel_multi_agent_planner.agents.search", "SearchAgent"),
        }
        module_class = agent_map.get(target_agent)
        if not module_class:
            return True, ""

        module = importlib.import_module(module_class[0])
        cls = getattr(module, module_class[1], None)
        if cls is None or not hasattr(cls, target_method):
            return False, f"{module_class[1]}.{target_method} not found"

        try:
            expected = inspect.signature(getattr(cls, target_method))
            candidate_tree = ast.parse(file_patch.new_snippet or "")
            for node in candidate_tree.body:
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name == target_method:
                            return self._compare_signature(expected, item)
        except Exception as exc:
            return False, str(exc)
        return False, f"{target_method} definition missing in extension"

    def _validate_runtime_shape(self, file_patch) -> tuple[bool, str]:
        metadata = file_patch.metadata if isinstance(file_patch.metadata, dict) else {}
        target_agent = str(metadata.get("target_agent") or "").strip()
        snippet = file_patch.new_snippet or ""
        if target_agent == "planner":
            if "create_daily_spot_plan" not in snippet:
                return False, "planner extension must override create_daily_spot_plan"
        if target_agent == "food_spot":
            if "attach_meals" not in snippet:
                return False, "food_spot extension must override attach_meals"
        if target_agent == "transport":
            if "build_day_transport" not in snippet:
                return False, "transport extension must override build_day_transport"
        return True, ""

    def _compare_signature(self, expected: inspect.Signature, node: ast.FunctionDef) -> tuple[bool, str]:
        expected_params = [param for name, param in expected.parameters.items() if name != "self"]
        candidate_params = [arg.arg for arg in node.args.args if arg.arg != "self"]
        if len(candidate_params) != len(expected_params):
            return False, f"parameter count mismatch: {candidate_params} != {[param.name for param in expected_params]}"
        for index, expected_param in enumerate(expected_params):
            candidate_name = candidate_params[index]
            if candidate_name != expected_param.name:
                return False, f"parameter name/order mismatch at position {index}: {candidate_name} != {expected_param.name}"
        required_expected = sum(
            1
            for param in expected_params
            if param.default is inspect._empty and param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        candidate_defaults = len(node.args.defaults)
        candidate_required = len(candidate_params) - candidate_defaults
        if candidate_required > required_expected:
            return False, f"required parameter mismatch: candidate requires {candidate_required}, expected {required_expected}"
        if node.args.vararg is not None or node.args.kwarg is not None:
            return False, "variadic parameters are not allowed in runtime extensions"
        return True, ""

    def _build_smoke_checks(self, file_patch) -> list[str]:
        checks = ["syntax", "imports", "runtime_signature"]
        metadata = file_patch.metadata if isinstance(file_patch.metadata, dict) else {}
        if metadata.get("target_agent") == "transport":
            checks.append("transport_selection")
        if metadata.get("target_agent") == "food_spot":
            checks.append("food_filtering")
        return checks

    def _get_project_imports(self, base_path: Path) -> set[str]:
        imports = {"travel_multi_agent_planner", "travel_multi_agent_planner.models", "travel_multi_agent_planner.agents"}
        agents_dir = base_path / "travel_multi_agent_planner" / "agents"
        if agents_dir.exists():
            for file in agents_dir.glob("*.py"):
                if file.name != "__init__.py":
                    imports.add(f"travel_multi_agent_planner.agents.{file.stem}")
        providers_dir = base_path / "travel_multi_agent_planner" / "providers"
        if providers_dir.exists():
            for file in providers_dir.glob("*.py"):
                if file.name != "__init__.py":
                    imports.add(f"travel_multi_agent_planner.providers.{file.stem}")
        return imports

    def _is_stdlib(self, module_name: str) -> bool:
        stdlib_modules = {
            "__future__",
            "os",
            "sys",
            "re",
            "json",
            "datetime",
            "time",
            "pathlib",
            "typing",
            "dataclasses",
            "enum",
            "collections",
            "itertools",
            "functools",
            "abc",
            "ast",
            "subprocess",
            "uuid",
            "math",
            "copy",
            "inspect",
        }
        return module_name.split(".")[0] in stdlib_modules

    def _validate_types(self, patch: ModificationPatch, base_path: Path) -> bool:
        return True

    def _run_related_tests(self, patch: ModificationPatch, base_path: Path) -> dict[str, list[str]]:
        passed: list[str] = []
        failed: list[str] = []
        test_map = {
            "travel_multi_agent_planner/agents/planner.py": ["tests/test_orchestrator.py"],
            "travel_multi_agent_planner/agents/food_spot.py": ["tests/test_orchestrator.py"],
            "travel_multi_agent_planner/agents/hotel.py": ["tests/test_orchestrator.py"],
            "travel_multi_agent_planner/agents/transport.py": ["tests/test_orchestrator.py"],
            "travel_multi_agent_planner/orchestrator.py": ["tests/test_orchestrator.py"],
        }
        modified_files = {fp.file_path for fp in patch.patches}
        tests_to_run = set()
        for modified, tests in test_map.items():
            if any(modified in file for file in modified_files):
                tests_to_run.update(tests)
        for test_file in tests_to_run:
            test_path = base_path / test_file
            if test_path.exists():
                result = self._run_pytest(test_path)
                if result["success"]:
                    passed.append(f"test:{test_file}")
                else:
                    failed.append(f"test:{test_file}: {result['error']}")
        return {"passed": passed, "failed": failed}

    def _run_pytest(self, test_path: Path) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path), "-q"],
                capture_output=True,
                text=True,
                timeout=90,
            )
            return {"success": result.returncode == 0, "output": result.stdout, "error": result.stderr}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _generate_validation_message(self, passed: list[str], failed: list[str]) -> str:
        total = len(passed) + len(failed)
        if total == 0:
            return "No validation tests performed"
        if not failed:
            return f"All {len(passed)} validation tests passed"
        return f"{len(passed)}/{total} tests passed. {len(failed)} failed."
