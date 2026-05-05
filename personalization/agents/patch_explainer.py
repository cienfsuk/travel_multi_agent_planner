"""Patch Explainer Agent - produces user-facing summaries of generated personalization patches."""

from __future__ import annotations

from typing import Any

from ..models import ImpactReport, ModificationPatch, ReviewResult, ValidationResult


class PatchExplainerAgent:
    """Summarize patch outcome in a front-end friendly format."""

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider

    def explain(
        self,
        raw_requirement: str,
        patch: ModificationPatch | None,
        impact: ImpactReport | None,
        review: ReviewResult | None,
        validation: ValidationResult | None = None,
        repair_attempts: int = 0,
        final_generation_source: str = "",
    ) -> dict[str, Any]:
        summary = self._build_summary(raw_requirement, impact, review, repair_attempts, final_generation_source)
        return {
            "summary": summary,
            "patch_count": len(patch.patches) if patch else 0,
            "repair_attempts": repair_attempts,
            "final_generation_source": final_generation_source,
            "validation_message": validation.message if validation else "",
            "review_recommendation": review.recommendation if review else "",
        }

    def _build_summary(
        self,
        raw_requirement: str,
        impact: ImpactReport | None,
        review: ReviewResult | None,
        repair_attempts: int,
        final_generation_source: str,
    ) -> str:
        parts = [f"已根据“{raw_requirement}”生成个性化修改方案。"]
        if impact:
            parts.append(f"影响 {len(impact.impacted_files)} 个文件，风险等级为 {impact.risk_level.value}。")
        if review:
            parts.append("代码审查通过。" if review.passed else "代码审查存在问题。")
        if repair_attempts:
            parts.append(f"期间触发了 {repair_attempts} 次自动修复。")
        if final_generation_source:
            source_labels = {"llm": "原始大模型生成", "template": "规则生成", "repaired": "修复后生成"}
            parts.append(f"最终采用 {source_labels.get(final_generation_source, final_generation_source)}。")
        return " ".join(parts)
