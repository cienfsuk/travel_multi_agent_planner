"""Code Review Agent - reviews code modifications for correctness and safety."""

from __future__ import annotations

import ast
from typing import Any

from ..models import (
    ImpactReport,
    ModificationPatch,
    ReviewIssue,
    ReviewIssueSeverity,
    ReviewResult,
)


class CodeReviewAgent:
    """Reviews patches using static checks and optional LLM review."""

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider

    async def review(self, patch: ModificationPatch, impact: ImpactReport) -> ReviewResult:
        issues: list[ReviewIssue] = []
        for file_patch in patch.patches:
            issues.extend(self._check_syntax(file_patch))
            issues.extend(self._check_security(file_patch))
            issues.extend(self._check_runtime_shape(file_patch))

        llm_used = False
        if self.llm and hasattr(self.llm, "generate_json"):
            llm_used = True
            issues.extend(await self._review_with_llm(patch, impact))

        has_errors = any(issue.severity == ReviewIssueSeverity.ERROR for issue in issues)
        has_warnings = any(issue.severity == ReviewIssueSeverity.WARNING for issue in issues)
        can_auto_apply = not has_errors and impact.risk_level.value != "high"

        if has_errors:
            recommendation = "revise"
        elif has_warnings:
            recommendation = "apply_with_caution"
        else:
            recommendation = "apply"

        return ReviewResult(
            passed=not has_errors,
            issues=issues,
            can_auto_apply=can_auto_apply,
            recommendation=recommendation,
            llm_review_used=llm_used,
            repair_recommended=has_errors or has_warnings,
        )

    def _check_syntax(self, file_patch) -> list[ReviewIssue]:
        if not file_patch.file_path.endswith(".py") or not file_patch.new_snippet:
            return []
        try:
            ast.parse(file_patch.new_snippet)
            return []
        except SyntaxError as exc:
            return [
                ReviewIssue(
                    severity=ReviewIssueSeverity.ERROR,
                    category="syntax",
                    file=file_patch.file_path,
                    line=exc.lineno,
                    message=f"语法错误: {exc.msg}",
                    suggestion="修正语法后重新提交",
                )
            ]

    def _check_security(self, file_patch) -> list[ReviewIssue]:
        if not file_patch.new_snippet:
            return []
        patterns = [
            (r"eval\s*\(", "使用 eval() 可能导致安全风险"),
            (r"exec\s*\(", "使用 exec() 可能导致安全风险"),
            (r"os\.system\s*\(", "使用 os.system() 可能导致命令注入"),
            (r"subprocess\s*\(", "使用 subprocess 可能需要额外审查"),
        ]
        issues: list[ReviewIssue] = []
        for pattern, message in patterns:
            if __import__("re").search(pattern, file_patch.new_snippet):
                issues.append(
                    ReviewIssue(
                        severity=ReviewIssueSeverity.WARNING,
                        category="security",
                        file=file_patch.file_path,
                        message=message,
                        suggestion="改用更安全的实现",
                    )
                )
        return issues

    def _check_runtime_shape(self, file_patch) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        metadata = file_patch.metadata if isinstance(file_patch.metadata, dict) else {}
        target_agent = metadata.get("target_agent", "")
        target_method = metadata.get("target_method", "")
        if target_agent and target_method and target_method not in file_patch.new_snippet:
            issues.append(
                ReviewIssue(
                    severity=ReviewIssueSeverity.WARNING,
                    category="runtime_shape",
                    file=file_patch.file_path,
                    message=f"未明显看到目标方法 {target_method} 的重写逻辑。",
                    suggestion="确认 method signature 与真实 agent API 一致",
                )
            )
        if "super()." not in file_patch.new_snippet and target_agent in {"planner", "transport", "hotel", "search", "budget"}:
            issues.append(
                ReviewIssue(
                    severity=ReviewIssueSeverity.WARNING,
                    category="runtime_shape",
                    file=file_patch.file_path,
                    message="扩展代码未明显调用 super()。",
                    suggestion="在保留原逻辑的基础上做最小改动",
                )
            )
        return issues

    async def _review_with_llm(self, patch: ModificationPatch, impact: ImpactReport) -> list[ReviewIssue]:
        if not self.llm or not hasattr(self.llm, "generate_json"):
            return []

        system_prompt = (
            "你是代码审查 Agent。"
            "请检查个性化扩展是否覆盖需求、是否与真实 agent 方法签名兼容、是否存在回归风险。"
            "只返回 JSON 数组，每项包含 severity/category/message/suggestion。"
        )
        payload = {
            "files": [fp.file_path for fp in patch.patches],
            "impact": impact.to_dict(),
            "code": self._format_patch_for_review(patch),
        }
        result = self.llm.generate_json(system_prompt, payload, schema_hint="[]", temperature=0.1)
        if not isinstance(result, list):
            return []

        issues: list[ReviewIssue] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            severity = self._severity_from_string(str(item.get("severity", "warning")))
            issues.append(
                ReviewIssue(
                    severity=severity,
                    category=str(item.get("category", "general")),
                    file=patch.patches[0].file_path if patch.patches else "",
                    message=str(item.get("message", "")),
                    suggestion=str(item.get("suggestion", "")),
                )
            )
        return issues

    def _severity_from_string(self, value: str) -> ReviewIssueSeverity:
        mapping = {
            "error": ReviewIssueSeverity.ERROR,
            "warning": ReviewIssueSeverity.WARNING,
            "info": ReviewIssueSeverity.INFO,
        }
        return mapping.get(value.lower(), ReviewIssueSeverity.WARNING)

    def _format_patch_for_review(self, patch: ModificationPatch) -> str:
        lines: list[str] = []
        for file_patch in patch.patches:
            lines.append(f"=== {file_patch.file_path} ===")
            lines.append(file_patch.new_snippet)
        return "\n".join(lines)
