"""Impact Analyzer Agent - analyzes the impact of code modifications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import ImpactReport, ModificationPatch, RiskLevel


class ImpactAnalyzerAgent:
    """
    Analyzes the potential impact of a modification on the system.

    Examines which files, agents, and modules might be affected and
    assesses the risk level of the proposed change.
    """

    # Dependency graph: file -> list of files that depend on it
    DEPENDENCY_MAP = {
        "travel_multi_agent_planner/agents/planner.py": [
            "travel_multi_agent_planner/orchestrator.py",
            "travel_multi_agent_planner/agents/validator.py",
        ],
        "travel_multi_agent_planner/agents/food_spot.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/agents/hotel.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/agents/transport.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/agents/requirement.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/agents/search.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/agents/budget.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/agents/validator.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/agents/web_guide.py": [
            "travel_multi_agent_planner/orchestrator.py",
        ],
        "travel_multi_agent_planner/scheduling.py": [
            "travel_multi_agent_planner/orchestrator.py",
            "travel_multi_agent_planner/agents/planner.py",
        ],
        "travel_multi_agent_planner/config.py": [
            "travel_multi_agent_planner/orchestrator.py",
            "backend/main.py",
        ],
        "travel_multi_agent_planner/models.py": [
            "travel_multi_agent_planner/orchestrator.py",
            "travel_multi_agent_planner/agents/*.py",
        ],
    }

    # Agent dependencies: file -> list of agents that might be affected
    AGENT_DEPENDENCY_MAP = {
        "travel_multi_agent_planner/agents/planner.py": ["ValidatorAgent", "TransportAgent"],
        "travel_multi_agent_planner/agents/food_spot.py": ["ValidatorAgent", "TransportAgent"],
        "travel_multi_agent_planner/agents/hotel.py": ["BudgetAgent"],
        "travel_multi_agent_planner/agents/transport.py": ["PlannerAgent", "ValidatorAgent"],
        "travel_multi_agent_planner/scheduling.py": ["PlannerAgent", "FoodSpotAgent"],
        "travel_multi_agent_planner/models.py": ["All agents"],
    }

    # Critical files that changes could significantly impact
    CRITICAL_FILES = {
        "travel_multi_agent_planner/orchestrator.py",
        "travel_multi_agent_planner/models.py",
        "travel_multi_agent_planner/app.py",
    }

    def __init__(self):
        """Initialize the impact analyzer."""
        pass

    def analyze(self, patch: ModificationPatch, base_path: Path) -> ImpactReport:
        """
        Analyze the impact of a modification patch.

        Args:
            patch: ModificationPatch to analyze
            base_path: Base path of the project

        Returns:
            ImpactReport with analysis results
        """
        impacted_files: set[str] = set()
        impacted_agents: set[str] = set()
        impacted_modules: set[str] = set()

        for file_patch in patch.patches:
            impacted_files.add(file_patch.file_path)

            # Find dependent files
            if file_patch.file_path in self.DEPENDENCY_MAP:
                impacted_files.update(self.DEPENDENCY_MAP[file_patch.file_path])

            # Find related modules
            module = self._extract_module(file_patch.file_path)
            if module:
                impacted_modules.add(module)

            # Find related agents
            if file_patch.file_path in self.AGENT_DEPENDENCY_MAP:
                impacted_agents.update(self.AGENT_DEPENDENCY_MAP[file_patch.file_path])

        # Analyze risk level
        risk_level = self._assess_risk(patch, impacted_files)

        # Check backward compatibility
        backward_compatible = self._check_compatibility(patch, impacted_files)

        # Generate summary
        summary = self._generate_summary(patch, impacted_files, impacted_agents, risk_level)

        return ImpactReport(
            impacted_files=list(impacted_files),
            impacted_agents=list(impacted_agents),
            impacted_modules=list(impacted_modules),
            risk_level=risk_level,
            backward_compatible=backward_compatible,
            summary=summary,
        )

    def _extract_module(self, file_path: str) -> str | None:
        """Extract module name from file path."""
        parts = file_path.split("/")
        if len(parts) >= 3:
            return parts[-2]  # Return the parent directory name
        return None

    def _assess_risk(self, patch: ModificationPatch, impacted_files: set[str]) -> RiskLevel:
        """Assess the risk level of the modification."""
        # High risk: delete operations or critical files
        for file_patch in patch.patches:
            if file_patch.operation.value in ("delete", "replace"):
                return RiskLevel.HIGH

            if file_patch.file_path in self.CRITICAL_FILES:
                return RiskLevel.HIGH

        # Medium risk: modifications to orchestrator or core models
        if any(f in impacted_files for f in self.CRITICAL_FILES):
            return RiskLevel.MEDIUM

        # Low risk: agent-specific files
        return RiskLevel.LOW

    def _check_compatibility(self, patch: ModificationPatch, impacted_files: set[str]) -> bool:
        """
        Check if the modification maintains backward compatibility.

        Returns True if changes are backward compatible.
        """
        # Check if critical interfaces are maintained
        for file_patch in patch.patches:
            if file_patch.file_path == "travel_multi_agent_planner/models.py":
                # Check if dataclass fields are not removed
                if file_patch.operation.value == "modify":
                    # Need more detailed analysis
                    pass

        return True  # Simplified for now

    def _generate_summary(
        self, patch: ModificationPatch, impacted_files: set[str], impacted_agents: set[str], risk_level: RiskLevel
    ) -> str:
        """Generate a human-readable summary of the impact."""
        summary_parts = []

        # File count summary
        summary_parts.append(f"修改涉及 {len(impacted_files)} 个文件")

        # Agent impact
        if impacted_agents:
            summary_parts.append(f"可能影响 {len(impacted_agents)} 个 Agent: {', '.join(impacted_agents)}")

        # Risk level
        risk_text = {"low": "低", "medium": "中", "high": "高"}.get(risk_level.value, "未知")
        summary_parts.append(f"风险等级: {risk_text}")

        # Operation summary
        operations = {}
        for fp in patch.patches:
            op = fp.operation.value
            operations[op] = operations.get(op, 0) + 1

        op_parts = [f"{op}({count})" for op, count in operations.items()]
        summary_parts.append(f"操作: {', '.join(op_parts)}")

        return "，".join(summary_parts)

    def get_affected_test_files(self, impacted_files: list[str]) -> list[str]:
        """
        Get test files that might need to be run after the modification.

        Args:
            impacted_files: List of impacted file paths

        Returns:
            List of test file paths that should be run
        """
        test_files = []
        test_map = {
            "travel_multi_agent_planner/agents/planner.py": ["tests/test_planner.py"],
            "travel_multi_agent_planner/agents/food_spot.py": ["tests/test_food_spot.py"],
            "travel_multi_agent_planner/orchestrator.py": ["tests/test_orchestrator.py"],
        }

        for impacted in impacted_files:
            if impacted in test_map:
                test_files.extend(test_map[impacted])

        return list(set(test_files))