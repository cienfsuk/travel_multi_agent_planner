"""Personalization Engine - orchestrates the fixed multi-agent personalization workflow."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from .agents.code_modifier import (
    CodeModifierAgent,
    RUNTIME_AGENT_SPECS,
    ensure_extension_runtime,
    sync_runtime_agent_bindings,
    unload_extension_modules,
    upgrade_saved_extension_file,
)
from .agents.code_fixer import CodeFixerAgent
from .agents.code_generator import CodeGeneratorAgent
from .agents.code_planner import CodePlanningAgent
from .agents.code_reviewer import CodeReviewAgent
from .agents.code_validator import CodeValidatorAgent
from .agents.impact_analyzer import ImpactAnalyzerAgent
from .agents.patch_explainer import PatchExplainerAgent
from .agents.requirement_parser import RequirementParserAgent
from .agents.requirement_splitter import RequirementSplitterAgent
from .agents.target_resolver import TargetResolverAgent
from .agents.version_manager import VersionManagerAgent
from .models import (
    AgentTraceItem,
    ApplyResult,
    ImpactReport,
    ModificationPatch,
    ModificationType,
    ParsedRequirement,
    PatchMetadata,
    PersonalizationResult,
    ReviewIssueSeverity,
    ReviewResult,
    RiskLevel,
    ValidationResult,
)


class PersonalizationEngine:
    """Coordinate the fixed personalization pipeline."""

    def __init__(self, base_path: Path, llm_provider: Any = None, patches_dir: Path | None = None):
        self.base_path = Path(base_path)
        self.llm = llm_provider
        if patches_dir is None:
            patches_dir = self.base_path / "personalization" / "patches"

        self.requirement_parser = RequirementParserAgent(llm_provider)
        self.requirement_splitter = RequirementSplitterAgent(llm_provider)
        self.target_resolver = TargetResolverAgent(llm_provider)
        self.code_planner = CodePlanningAgent(llm_provider)
        self.code_generator = CodeGeneratorAgent(llm_provider)
        self.code_fixer = CodeFixerAgent(llm_provider)
        self.code_modifier = CodeModifierAgent(llm_provider)
        self.impact_analyzer = ImpactAnalyzerAgent()
        self.code_reviewer = CodeReviewAgent(llm_provider)
        self.code_validator = CodeValidatorAgent()
        self.patch_explainer = PatchExplainerAgent(llm_provider)
        self.version_manager = VersionManagerAgent(patches_dir)

        self._pending_results: dict[str, PersonalizationResult] = {}
        self._load_saved_extensions()

    async def process_requirement(self, user_text: str, context: dict | None = None) -> PersonalizationResult:
        agent_trace: list[AgentTraceItem] = []
        stage_statuses: dict[str, str] = {}
        try:
            parsed = self.requirement_parser.parse(user_text, context)
            self._trace(
                agent_trace,
                stage_statuses,
                stage="parser",
                agent="RequirementParserAgent",
                status="ok",
                summary=f"Parsed requirement into {len(parsed.target_files)} target file(s).",
                details={"target_files": parsed.target_files, "confidence": parsed.confidence},
            )

            tasks = self.requirement_splitter.split(user_text, parsed.parameters)
            if not tasks:
                tasks = [
                    {
                        "id": "sub_1",
                        "text": user_text.strip(),
                        "scope": {"days": [], "meals": []},
                        "dependency": "independent",
                        "source": "fallback",
                    }
                ]
            self._trace(
                agent_trace,
                stage_statuses,
                stage="splitter",
                agent="RequirementSplitterAgent",
                status="ok",
                summary=f"Split into {len(tasks)} sub-requirement(s).",
                details={"task_ids": [task["id"] for task in tasks]},
            )

            task_contexts: list[dict[str, Any]] = []
            sub_requirements: list[dict[str, Any]] = []
            sub_requirement_map: dict[str, dict[str, Any]] = {}

            for task in tasks:
                resolution = self.target_resolver.resolve(task, parsed.parameters)
                self._trace(
                    agent_trace,
                    stage_statuses,
                    stage="resolver",
                    agent="TargetResolverAgent",
                    status="ok",
                    summary=f"{task['id']} -> {resolution['target_agent']}.{resolution['target_method']}",
                    details={"task_id": task["id"], **resolution},
                )

                code_plan = self.code_planner.plan(task, resolution, parsed.parameters)
                self._trace(
                    agent_trace,
                    stage_statuses,
                    stage="planner",
                    agent="CodePlanningAgent",
                    status="ok",
                    summary=f"{task['id']} produced a structured code plan.",
                    details={"task_id": task["id"], "acceptance_checks": code_plan.get("acceptance_checks", [])},
                )

                task_contexts.append({"task": task, "resolution": resolution, "code_plan": code_plan})
                row = {
                    "id": task["id"],
                    "text": task["text"],
                    "scope": task.get("scope", {}),
                    "dependency": task.get("dependency", "independent"),
                    "target_agent": resolution["target_agent"],
                    "target_method": resolution["target_method"],
                    "change_strategy": resolution.get("change_strategy", "runtime_extension"),
                    "generation_source": "",
                    "attempt_count": 0,
                    "repair_attempts": 0,
                    "review_passed": False,
                    "validation_success": False,
                    "runtime_signature_ok": False,
                    "blocking_issues": [],
                }
                sub_requirements.append(row)
                sub_requirement_map[task["id"]] = row

            grouped_contexts = self._group_task_contexts(task_contexts)

            combined_patches: list[Any] = []
            impacts: list[ImpactReport] = []
            reviews: list[ReviewResult] = []
            validations: list[ValidationResult] = []
            blocking_issues: list[str] = []
            attempt_count = 0
            repair_attempts = 0
            final_generation_source = "template"

            for grouped in grouped_contexts:
                group_task = self._build_group_task(grouped)
                group_resolution = grouped[0]["resolution"]
                group_plan = self._build_group_plan(grouped)

                patch, generation_source = self.code_generator.generate(
                    requirement=parsed,
                    task=group_task,
                    resolution=group_resolution,
                    code_plan=group_plan,
                    base_path=self.base_path,
                )
                attempt_count += 1
                current_patch = patch
                current_source = generation_source
                impact = self.impact_analyzer.analyze(current_patch, self.base_path)
                review = await self.code_reviewer.review(current_patch, impact)
                validation = self.code_validator.validate(current_patch, self.base_path)
                self._trace(
                    agent_trace,
                    stage_statuses,
                    stage="codegen",
                    agent="CodeGeneratorAgent",
                    status="ok" if current_patch.patches else "blocked",
                    summary=f"{group_task['id']} generated {len(current_patch.patches)} patch file(s) from {current_source}.",
                    details={
                        "task_ids": [item["task"]["id"] for item in grouped],
                        "patch_count": len(current_patch.patches),
                        "generation_source": current_source,
                    },
                )

                local_repairs = 0
                while local_repairs < self.code_fixer.max_attempts and (not review.passed or not validation.success):
                    repaired_patch = self.code_fixer.repair(
                        requirement=parsed,
                        task=group_task,
                        resolution=group_resolution,
                        code_plan=group_plan,
                        current_patch=current_patch,
                        review_result=review,
                        validation_result=validation,
                        attempt_index=local_repairs,
                    )
                    if repaired_patch is None:
                        break

                    local_repairs += 1
                    repair_attempts += 1
                    attempt_count += 1
                    current_patch = repaired_patch
                    current_source = "repaired"
                    impact = self.impact_analyzer.analyze(current_patch, self.base_path)
                    review = await self.code_reviewer.review(current_patch, impact)
                    validation = self.code_validator.validate(current_patch, self.base_path)
                    self._trace(
                        agent_trace,
                        stage_statuses,
                        stage="repair",
                        agent="CodeFixerAgent",
                        status="ok" if review.passed and validation.success else "warning",
                        summary=f"{group_task['id']} repair attempt {local_repairs} completed.",
                        details={
                            "task_ids": [item["task"]["id"] for item in grouped],
                            "repair_attempt": local_repairs,
                            "validation_success": validation.success,
                            "review_passed": review.passed,
                        },
                    )

                combined_patches.extend(current_patch.patches)
                impacts.append(impact)
                reviews.append(review)
                validations.append(validation)
                final_generation_source = self._merge_generation_source(final_generation_source, current_source)

                self._trace(
                    agent_trace,
                    stage_statuses,
                    stage="review",
                    agent="CodeReviewAgent",
                    status="ok" if review.passed else "blocked",
                    summary=f"{group_task['id']} review recommendation: {review.recommendation}.",
                    details={"task_ids": [item["task"]["id"] for item in grouped], "issues": len(review.issues)},
                )
                self._trace(
                    agent_trace,
                    stage_statuses,
                    stage="validator",
                    agent="CodeValidatorAgent",
                    status="ok" if validation.success else "blocked",
                    summary=f"{group_task['id']} validation {'passed' if validation.success else 'failed'}.",
                    details={
                        "task_ids": [item["task"]["id"] for item in grouped],
                        "runtime_signature_ok": validation.runtime_signature_ok,
                        "tests_failed": validation.tests_failed,
                    },
                )

                for item in grouped:
                    task_id = item["task"]["id"]
                    task_blocking = self._collect_blocking_issues(task_id, review, validation)
                    row = sub_requirement_map[task_id]
                    row["generation_source"] = current_source
                    row["attempt_count"] = 1 + local_repairs
                    row["repair_attempts"] = local_repairs
                    row["review_passed"] = review.passed
                    row["validation_success"] = validation.success
                    row["runtime_signature_ok"] = validation.runtime_signature_ok
                    row["blocking_issues"] = task_blocking
                    blocking_issues.extend(task_blocking)

            if not combined_patches:
                return self._error_result(
                    user_text=user_text,
                    message="No modification patch was generated.",
                    agent_trace=agent_trace,
                    stage_statuses=stage_statuses,
                )

            combined_patch = ModificationPatch(
                patches=combined_patches,
                metadata=PatchMetadata(description=f"Combined personalization patch for: {user_text}"),
                requirement_id=parsed.requirement_id,
            )
            combined_impact = self._combine_impacts(impacts)
            combined_review = self._combine_reviews(reviews)
            combined_validation = self._combine_validations(validations)

            explanation = self.patch_explainer.explain(
                raw_requirement=user_text,
                patch=combined_patch,
                impact=combined_impact,
                review=combined_review,
                validation=combined_validation,
                repair_attempts=repair_attempts,
                final_generation_source=final_generation_source,
            )

            self._trace(
                agent_trace,
                stage_statuses,
                stage="explainer",
                agent="PatchExplainerAgent",
                status="ok",
                summary="Generated the final user-facing patch explanation.",
                details={"blocking_issue_count": len(blocking_issues)},
            )

            result = PersonalizationResult(
                parsed_requirement=parsed,
                modification_patch=combined_patch,
                impact_report=combined_impact,
                review_result=combined_review,
                requires_confirmation=True,
                status="pending_review" if blocking_issues else "pending_approval",
                agent_trace=agent_trace,
                sub_requirements=sub_requirements,
                attempt_count=attempt_count,
                repair_attempts=repair_attempts,
                final_generation_source=final_generation_source,
                stage_statuses=stage_statuses,
                blocking_issues=blocking_issues,
                explanation={**explanation, "validation": combined_validation.to_dict()},
            )
            self._pending_results[parsed.requirement_id] = result
            return result
        except Exception as exc:
            return self._error_result(
                user_text=user_text,
                message=str(exc),
                agent_trace=agent_trace,
                stage_statuses=stage_statuses,
            )

    async def apply_modification(
        self, requirement_id: str, approved: bool, user_notes: str = "", save_extensions: bool = True
    ) -> ApplyResult:
        if not approved:
            return ApplyResult(success=True, apply_message="Modification cancelled by user", status="cancelled")

        result = self._pending_results.get(requirement_id)
        if result is None:
            return ApplyResult(success=False, apply_message="Requirement not found", status="error")
        if result.modification_patch is None:
            return ApplyResult(success=False, apply_message="No patch generated", status="error")
        if result.blocking_issues:
            return ApplyResult(
                success=False,
                apply_message="Patch is blocked by review or validation issues.",
                status="blocked",
                blocking_issues=result.blocking_issues,
            )

        validation = self.code_validator.validate(result.modification_patch, self.base_path)
        if not validation.success or not validation.runtime_signature_ok:
            blocking = list(validation.tests_failed)
            if not validation.runtime_signature_ok:
                blocking.append("runtime_signature_mismatch")
            return ApplyResult(
                success=False,
                apply_message="Patch failed validation and was not applied.",
                validation_result=validation,
                status="blocked",
                blocking_issues=blocking,
            )

        try:
            snapshot = self.version_manager.create_snapshot(
                result.modification_patch, self.base_path, save_extensions=save_extensions
            )
            apply_result = self.code_modifier.apply(
                result.modification_patch,
                self.base_path,
                save_extensions=save_extensions,
            )
            if not apply_result.get("success"):
                return ApplyResult(
                    success=False,
                    snapshot_id=snapshot.id if snapshot else "",
                    apply_message="; ".join(apply_result.get("errors", [])) or "Failed to apply modification",
                    validation_result=validation,
                    status="error",
                )

            self.code_modifier.save_patch(result.modification_patch, self.base_path / "personalization" / "patches")
            result.status = "applied"
            return ApplyResult(
                success=True,
                snapshot_id=snapshot.id if snapshot else "",
                apply_message=f"Applied {len(result.modification_patch.patches)} changes",
                validation_result=validation,
                status="applied",
            )
        except Exception as exc:
            return ApplyResult(
                success=False,
                apply_message=f"Failed to apply modification: {exc}",
                validation_result=validation,
                status="error",
            )

    async def rollback_modification(self, snapshot_id: str) -> dict[str, Any]:
        return self.version_manager.rollback(snapshot_id, self.base_path).to_dict()

    def list_pending_requirements(self) -> list[dict[str, Any]]:
        return [
            result.to_dict()
            for result in self._pending_results.values()
            if result.status in {"pending_approval", "pending_review"}
        ]

    def get_snapshot_history(self) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for snapshot in self.version_manager.list_snapshots():
            info = self.version_manager.get_snapshot_info(snapshot.id)
            if info is not None:
                history.append(info)
        return history

    def clear_processed(self, requirement_id: str) -> bool:
        if requirement_id in self._pending_results:
            del self._pending_results[requirement_id]
            return True
        return False

    def export_result(self, requirement_id: str, output_path: Path) -> bool:
        result = self._pending_results.get(requirement_id)
        if result is None:
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def get_status(self) -> dict[str, Any]:
        return {
            "pending_count": len(
                [result for result in self._pending_results.values() if result.status in {"pending_approval", "pending_review"}]
            ),
            "processed_count": len([result for result in self._pending_results.values() if result.status == "applied"]),
            "snapshot_count": len(self.version_manager.list_snapshots()),
            "llm_available": self.llm is not None,
        }

    def get_active_extensions(self) -> list[str]:
        """Return list of currently loaded extension filenames."""
        extensions_dir = self.base_path / "personalization" / "extensions"
        if not extensions_dir.exists():
            return []
        return [f.name for f in extensions_dir.glob("*.py") if not f.name.startswith("_")]

    def clear_all_extensions(self) -> dict[str, Any]:
        """
        Remove all generated extension files and restore original agent classes.
        Also clears all pending results and snapshots.
        """
        import travel_multi_agent_planner.agents as agents_pkg
        from personalization.agents.code_modifier import ORIGINAL_RUNTIME_AGENT_CLASSES, sync_runtime_agent_bindings

        results: dict[str, str] = {}

        # Remove extension files
        extensions_dir = self.base_path / "personalization" / "extensions"
        if extensions_dir.exists():
            for ext_file in extensions_dir.glob("*.py"):
                if ext_file.name.startswith("_"):
                    continue
                try:
                    ext_file.unlink()
                    results[ext_file.name] = "deleted"
                except Exception as e:
                    results[ext_file.name] = f"error: {e}"

        unload_extension_modules()

        # Restore original agent classes (remove monkey patches)
        for agent_name, (module_path, class_name) in RUNTIME_AGENT_SPECS.items():
            try:
                module = importlib.import_module(module_path)
                original_class = ORIGINAL_RUNTIME_AGENT_CLASSES.get(agent_name)
                if original_class is None:
                    continue
                setattr(agents_pkg, class_name, original_class)
                setattr(module, class_name, original_class)
            except Exception:
                pass

        sync_runtime_agent_bindings()

        # Clear pending results
        self._pending_results.clear()

        # Clear snapshots via version manager
        try:
            for snapshot in self.version_manager.list_snapshots():
                self.version_manager.delete_snapshot(snapshot.id)
        except Exception:
            pass

        return {
            "extensions_cleared": results,
            "pending_cleared": len(self._pending_results),
            "snapshots_cleared": True,
        }

    def _group_task_contexts(self, task_contexts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in task_contexts:
            target_agent = str(item["resolution"].get("target_agent") or "planner")
            grouped.setdefault(target_agent, []).append(item)
        return list(grouped.values())

    def _build_group_task(self, grouped: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": "__".join(item["task"]["id"] for item in grouped),
            "text": "；".join(str(item["task"].get("text") or "") for item in grouped),
            "scope": self._merge_scope([item["task"].get("scope", {}) for item in grouped]),
            "dependency": "independent",
            "source": "grouped",
        }

    def _build_group_plan(self, grouped: list[dict[str, Any]]) -> dict[str, Any]:
        resolution = grouped[0]["resolution"]
        acceptance_checks: list[str] = []
        seen_checks: set[str] = set()
        for item in grouped:
            for check in item["code_plan"].get("acceptance_checks", []):
                check_text = str(check).strip()
                if check_text and check_text not in seen_checks:
                    seen_checks.add(check_text)
                    acceptance_checks.append(check_text)
        task_items = [
            {
                "id": item["task"]["id"],
                "text": item["task"]["text"],
                "scope": item["task"].get("scope", {}),
                "expected_behavior": item["code_plan"].get("expected_behavior", item["task"]["text"]),
            }
            for item in grouped
        ]
        expected_behavior = "；".join(item["expected_behavior"] for item in task_items if item.get("expected_behavior"))
        return {
            "target_agent": resolution["target_agent"],
            "target_method": resolution["target_method"],
            "change_strategy": resolution.get("change_strategy", "runtime_extension"),
            "expected_behavior": expected_behavior,
            "patch_style": "override_and_super",
            "acceptance_checks": acceptance_checks,
            "scope": self._merge_scope([item["scope"] for item in task_items]),
            "source": "grouped",
            "task_items": task_items,
        }

    def _merge_scope(self, scopes: list[dict[str, Any]]) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {}
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            for key, value in scope.items():
                if not isinstance(value, list):
                    continue
                bucket = merged.setdefault(key, [])
                for item in value:
                    text = str(item).strip()
                    if text and text not in bucket:
                        bucket.append(text)
        return merged

    def _combine_impacts(self, impacts: list[ImpactReport]) -> ImpactReport | None:
        if not impacts:
            return None
        files = sorted({item for impact in impacts for item in impact.impacted_files})
        agents = sorted({item for impact in impacts for item in impact.impacted_agents})
        modules = sorted({item for impact in impacts for item in impact.impacted_modules})
        risk = self._max_risk([impact.risk_level for impact in impacts])
        backward_compatible = all(impact.backward_compatible for impact in impacts)
        summary = f"Combined {len(impacts)} impacts across {len(files)} files."
        return ImpactReport(
            impacted_files=files,
            impacted_agents=agents,
            impacted_modules=modules,
            risk_level=risk,
            backward_compatible=backward_compatible,
            summary=summary,
        )

    def _combine_reviews(self, reviews: list[ReviewResult]) -> ReviewResult:
        issues = [issue for review in reviews for issue in review.issues]
        passed = all(review.passed for review in reviews)
        can_auto_apply = all(review.can_auto_apply for review in reviews)
        llm_review_used = any(review.llm_review_used for review in reviews)
        repair_recommended = any(review.repair_recommended for review in reviews)
        recommendation = "apply"
        if not passed:
            recommendation = "revise"
        elif not can_auto_apply:
            recommendation = "apply_with_caution"
        return ReviewResult(
            passed=passed,
            issues=issues,
            can_auto_apply=can_auto_apply,
            recommendation=recommendation,
            llm_review_used=llm_review_used,
            repair_recommended=repair_recommended,
        )

    def _combine_validations(self, validations: list[ValidationResult]) -> ValidationResult:
        tests_passed = [item for validation in validations for item in validation.tests_passed]
        tests_failed = [item for validation in validations for item in validation.tests_failed]
        smoke_checks = [item for validation in validations for item in validation.smoke_checks]
        runtime_signature_ok = all(validation.runtime_signature_ok for validation in validations)
        success = all(validation.success for validation in validations)
        message = "All validation checks passed" if success else f"{len(tests_failed)} validation checks failed"
        return ValidationResult(
            success=success,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            can_revert=True,
            message=message,
            runtime_signature_ok=runtime_signature_ok,
            smoke_checks=smoke_checks,
        )

    def _collect_blocking_issues(
        self,
        task_id: str,
        review: ReviewResult,
        validation: ValidationResult,
    ) -> list[str]:
        issues: list[str] = []
        for issue in review.issues:
            if issue.severity == ReviewIssueSeverity.ERROR:
                issues.append(f"{task_id}: review:{issue.category}:{issue.message}")
        for failed in validation.tests_failed:
            issues.append(f"{task_id}: validation:{failed}")
        if not validation.runtime_signature_ok:
            issues.append(f"{task_id}: validation:runtime_signature_mismatch")
        return issues

    def _merge_generation_source(self, current: str, new: str) -> str:
        if new == "repaired":
            return "repaired"
        if current == "repaired":
            return current
        if new == "llm":
            return "llm"
        if current == "llm":
            return current
        return new or current

    def _max_risk(self, risks: list[RiskLevel]) -> RiskLevel:
        order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        return max(risks, key=lambda risk: order.get(risk, 0), default=RiskLevel.LOW)

    def _trace(
        self,
        traces: list[AgentTraceItem],
        stage_statuses: dict[str, str],
        *,
        stage: str,
        agent: str,
        status: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        traces.append(
            AgentTraceItem(
                stage=stage,
                agent=agent,
                status=status,
                summary=summary,
                details=details or {},
            )
        )
        previous = stage_statuses.get(stage)
        if previous in {"blocked", "failed"}:
            return
        if status in {"blocked", "failed"}:
            stage_statuses[stage] = status
        else:
            stage_statuses[stage] = "ok"

    def _error_result(
        self,
        *,
        user_text: str,
        message: str,
        agent_trace: list[AgentTraceItem],
        stage_statuses: dict[str, str],
    ) -> PersonalizationResult:
        return PersonalizationResult(
            parsed_requirement=ParsedRequirement(
                raw_text=user_text,
                target_files=[],
                modification_type=ModificationType.CODE,
                parameters={},
            ),
            status="error",
            error_message=message,
            agent_trace=agent_trace,
            stage_statuses=stage_statuses,
            blocking_issues=[message],
        )

    def _load_saved_extensions(self) -> None:
        extensions_dir = self.base_path / "personalization" / "extensions"
        if not extensions_dir.exists():
            return

        for ext_file in extensions_dir.glob("*.py"):
            if ext_file.name.startswith("_"):
                continue
            if ext_file.name.startswith("template_"):
                continue
            try:
                upgrade_saved_extension_file(ext_file)
                module_path = f"personalization.extensions.{ext_file.stem}"
                if module_path not in sys.modules:
                    base_str = str(self.base_path)
                    if base_str not in sys.path:
                        sys.path.insert(0, base_str)
                    module = importlib.import_module(module_path)
                    ensure_extension_runtime(module)
            except Exception as exc:
                print(f"Warning: Failed to load saved extension {ext_file.name}: {exc}")
        sync_runtime_agent_bindings()
