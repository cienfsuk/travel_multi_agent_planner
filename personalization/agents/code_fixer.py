"""Code Fixer Agent - targeted repair loop for failed personalization patches."""

from __future__ import annotations

from typing import Any

from ..models import ModificationPatch, ParsedRequirement, ReviewResult, ValidationResult
from .code_generator import CodeGeneratorAgent


class CodeFixerAgent:
    """Run bounded repair attempts based on review and validation failures."""

    def __init__(self, llm_provider: Any = None, max_attempts: int = 2):
        self.llm = llm_provider
        self.max_attempts = max_attempts
        self.generator = CodeGeneratorAgent(llm_provider)

    def repair(
        self,
        requirement: ParsedRequirement,
        task: dict[str, Any],
        resolution: dict[str, Any],
        code_plan: dict[str, Any],
        current_patch: ModificationPatch,
        review_result: ReviewResult | None,
        validation_result: ValidationResult | None,
        attempt_index: int,
    ) -> ModificationPatch | None:
        if attempt_index >= self.max_attempts:
            return None

        errors = self._collect_errors(review_result, validation_result)
        if not errors:
            return None

        repaired_patch, _ = self.generator.repair(
            requirement=requirement,
            task=task,
            resolution=resolution,
            code_plan=code_plan,
            current_patch=current_patch,
            errors=errors,
        )
        if repaired_patch is not None:
            return repaired_patch

        template_code = self.generator._build_runtime_template(
            requirement=requirement,
            task=task,
            resolution=resolution,
            code_plan=code_plan,
        )
        if not template_code:
            return None
        return self.generator._build_patch(
            requirement=requirement,
            task=task,
            resolution=resolution,
            code_plan=code_plan,
            code=template_code,
            source="repaired",
        )

    def _collect_errors(
        self,
        review_result: ReviewResult | None,
        validation_result: ValidationResult | None,
    ) -> list[str]:
        errors: list[str] = []
        if review_result:
            for issue in review_result.issues:
                errors.append(f"review:{issue.severity.value}:{issue.category}:{issue.message}")
        if validation_result:
            errors.extend(f"validation:{item}" for item in validation_result.tests_failed)
            if not validation_result.runtime_signature_ok:
                errors.append("validation:runtime_signature_mismatch")
        return errors
