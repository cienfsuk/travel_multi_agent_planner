"""Personalization extension module.

This module provides personalized customization capabilities for the
Travel Multi-Agent Planner system.

Usage:
    from personalization import PersonalizationEngine

    engine = PersonalizationEngine(base_path)
    result = await engine.process_requirement("不需要吃早饭")

    # Display result to user for confirmation
    if result.requires_confirmation:
        # User reviews and approves
        await engine.apply_modification(result.parsed_requirement.requirement_id, approved=True)
"""

from .engine import PersonalizationEngine
from .models import (
    ApplyResult,
    ImpactReport,
    ModificationPatch,
    ModificationType,
    ParsedRequirement,
    PatchOperation,
    PatchStatus,
    PersonalizationResult,
    ReviewIssue,
    ReviewResult,
    RiskLevel,
    RollbackResult,
    ValidationResult,
    VersionSnapshot,
)

__all__ = [
    "PersonalizationEngine",
    "ParsedRequirement",
    "ModificationPatch",
    "ModificationType",
    "PatchOperation",
    "PatchStatus",
    "ImpactReport",
    "ReviewIssue",
    "ReviewResult",
    "ValidationResult",
    "VersionSnapshot",
    "PersonalizationResult",
    "ApplyResult",
    "RollbackResult",
    "RiskLevel",
]