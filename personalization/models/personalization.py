"""Personalization extension module data models."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ModificationType(Enum):
    """Type of modification to be made."""

    CONFIG = "config"  # Configuration parameter change
    CODE = "code"  # Existing code logic change
    AGENT = "agent"  # Agent behavior/prompt change
    NEW_MODULE = "new_module"  # New module/file creation


class PatchOperation(Enum):
    """Operation type for a file patch."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    REPLACE = "replace"


class PatchStatus(Enum):
    """Status of a modification patch."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


class RiskLevel(Enum):
    """Risk level of a modification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ParsedRequirement:
    """Parsed user requirement converted to structured operation."""

    raw_text: str
    target_files: list[str]
    modification_type: ModificationType
    parameters: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    alternatives: list[ParsedRequirement] = field(default_factory=list)
    requirement_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class FilePatch:
    """A patch to be applied to a single file."""

    file_path: str
    operation: PatchOperation
    original_snippet: str = ""
    new_snippet: str = ""
    diff_lines: list[str] = field(default_factory=list)
    line_range: tuple[int, int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "operation": self.operation.value,
            "original_snippet": self.original_snippet,
            "new_snippet": self.new_snippet,
            "diff_lines": self.diff_lines,
            "line_range": self.line_range,
            "metadata": self.metadata,
        }


@dataclass
class PatchMetadata:
    """Metadata for a modification patch."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    author: str = "personalization_engine"
    description: str = ""


@dataclass
class ModificationPatch:
    """A collection of file patches representing a complete modification."""

    patches: list[FilePatch]
    metadata: PatchMetadata = field(default_factory=PatchMetadata)
    status: PatchStatus = PatchStatus.PENDING
    requirement_id: str = ""
    patch_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "requirement_id": self.requirement_id,
            "patches": [p.to_dict() for p in self.patches],
            "metadata": {
                "created_at": self.metadata.created_at,
                "author": self.metadata.author,
                "description": self.metadata.description,
            },
            "status": self.status.value,
        }


@dataclass
class ImpactReport:
    """Report analyzing the impact of a modification."""

    impacted_files: list[str]
    impacted_agents: list[str] = field(default_factory=list)
    impacted_modules: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    backward_compatible: bool = True
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "impacted_files": self.impacted_files,
            "impacted_agents": self.impacted_agents,
            "impacted_modules": self.impacted_modules,
            "risk_level": self.risk_level.value,
            "backward_compatible": self.backward_compatible,
            "summary": self.summary,
        }


class ReviewIssueSeverity(Enum):
    """Severity of a review issue."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ReviewIssue:
    """A single issue found during code review."""

    severity: ReviewIssueSeverity
    category: str
    file: str
    line: int | None = None
    message: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "category": self.category,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class ReviewResult:
    """Result of code review."""

    passed: bool
    issues: list[ReviewIssue] = field(default_factory=list)
    can_auto_apply: bool = True
    recommendation: str = "apply"  # apply, revise, reject
    llm_review_used: bool = False
    repair_recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
            "can_auto_apply": self.can_auto_apply,
            "recommendation": self.recommendation,
            "llm_review_used": self.llm_review_used,
            "repair_recommended": self.repair_recommended,
        }


@dataclass
class ValidationResult:
    """Result of functionality validation."""

    success: bool
    tests_passed: list[str] = field(default_factory=list)
    tests_failed: list[str] = field(default_factory=list)
    can_revert: bool = True
    message: str = ""
    runtime_signature_ok: bool = True
    smoke_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "can_revert": self.can_revert,
            "message": self.message,
            "runtime_signature_ok": self.runtime_signature_ok,
            "smoke_checks": self.smoke_checks,
        }


@dataclass
class AgentTraceItem:
    """Structured trace entry for the personalization multi-agent pipeline."""

    stage: str
    agent: str
    status: str
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "agent": self.agent,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass
class VersionSnapshot:
    """A snapshot of the system state for rollback."""

    id: str
    patch_id: str
    created_at: str
    backed_up_files: list[str] = field(default_factory=list)
    snapshot_path: str = ""
    description: str = ""
    save_extensions: bool = True  # Whether extension files were saved to disk

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "patch_id": self.patch_id,
            "created_at": self.created_at,
            "backed_up_files": self.backed_up_files,
            "snapshot_path": self.snapshot_path,
            "description": self.description,
            "save_extensions": self.save_extensions,
        }


@dataclass
class PersonalizationResult:
    """Complete result of processing a personalization request."""

    parsed_requirement: ParsedRequirement
    modification_patch: ModificationPatch | None = None
    impact_report: ImpactReport | None = None
    review_result: ReviewResult | None = None
    requires_confirmation: bool = True
    status: str = "pending"
    error_message: str = ""
    agent_trace: list[AgentTraceItem] = field(default_factory=list)
    sub_requirements: list[dict[str, Any]] = field(default_factory=list)
    attempt_count: int = 0
    repair_attempts: int = 0
    final_generation_source: str = ""
    stage_statuses: dict[str, str] = field(default_factory=dict)
    blocking_issues: list[str] = field(default_factory=list)
    explanation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.parsed_requirement.requirement_id,
            "raw_requirement": self.parsed_requirement.raw_text,
            "modification_type": self.parsed_requirement.modification_type.value if self.parsed_requirement.modification_type else None,
            "target_files": self.parsed_requirement.target_files,
            "modification_patch": self.modification_patch.to_dict() if self.modification_patch else None,
            "impact_report": self.impact_report.to_dict() if self.impact_report else None,
            "review_result": self.review_result.to_dict() if self.review_result else None,
            "requires_confirmation": self.requires_confirmation,
            "status": self.status,
            "error_message": self.error_message,
            "agent_trace": [item.to_dict() for item in self.agent_trace],
            "sub_requirements": self.sub_requirements,
            "attempt_count": self.attempt_count,
            "repair_attempts": self.repair_attempts,
            "final_generation_source": self.final_generation_source,
            "stage_statuses": self.stage_statuses,
            "blocking_issues": self.blocking_issues,
            "explanation": self.explanation,
        }


@dataclass
class ApplyResult:
    """Result of applying a modification."""

    success: bool
    snapshot_id: str = ""
    apply_message: str = ""
    validation_result: ValidationResult | None = None
    status: str = "unknown"
    blocking_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "snapshot_id": self.snapshot_id,
            "apply_message": self.apply_message,
            "validation_result": self.validation_result.to_dict() if self.validation_result else None,
            "status": self.status,
            "blocking_issues": self.blocking_issues,
        }


@dataclass
class RollbackResult:
    """Result of rolling back to a snapshot."""

    success: bool
    snapshot_id: str = ""
    reverted_files: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "snapshot_id": self.snapshot_id,
            "reverted_files": self.reverted_files,
            "error": self.error,
        }
