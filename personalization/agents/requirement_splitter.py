"""Requirement Splitter Agent - splits compound personalization requests into atomic tasks."""

from __future__ import annotations

import re
from typing import Any


class RequirementSplitterAgent:
    """Split compound requirements while preserving enough structure for downstream agents."""

    DAY_ORDER = {
        "第一天": "day1",
        "第二天": "day2",
        "第三天": "day3",
        "第四天": "day4",
        "第五天": "day5",
        "第六天": "day6",
        "第七天": "day7",
    }
    MEAL_KEYWORDS = {
        "早餐": "breakfast",
        "早饭": "breakfast",
        "午餐": "lunch",
        "午饭": "lunch",
        "中午": "lunch",
        "晚餐": "dinner",
        "晚饭": "dinner",
        "晚上": "dinner",
        "夜宵": "late_night",
        "宵夜": "late_night",
    }

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider

    def split(self, user_text: str, parsed_parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rule_tasks = self._split_with_rules(user_text)
        llm_tasks = self._split_with_llm(user_text, parsed_parameters or {})
        if llm_tasks and len(llm_tasks) >= len(rule_tasks):
            return llm_tasks
        return rule_tasks

    def _split_with_llm(self, user_text: str, parsed_parameters: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.llm or not hasattr(self.llm, "generate_json"):
            return []

        schema_hint = """
[
  {
    "text": "第一天晚上吃火锅",
    "scope": {"days": ["day1"], "meals": ["dinner"]},
    "dependency": "independent"
  }
]
""".strip()
        system_prompt = (
            "You split one Chinese travel personalization request into atomic executable sub-tasks. "
            "Preserve the original meaning and wording. "
            "Return only a JSON array. "
            "Do not claim the input is garbled unless it is actually unreadable."
        )
        result = self.llm.generate_json(
            system_prompt,
            {"user_text": user_text, "parsed_parameters": parsed_parameters},
            schema_hint=schema_hint,
            temperature=0.1,
        )
        if not isinstance(result, list):
            return []

        tasks: list[dict[str, Any]] = []
        for index, item in enumerate(result, start=1):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not self._valid_task_text(text, user_text):
                return []
            scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
            tasks.append(
                {
                    "id": f"sub_{index}",
                    "text": text,
                    "scope": {
                        "days": self._normalize_days(scope.get("days"), text),
                        "meals": self._normalize_meals(scope.get("meals"), text),
                    },
                    "dependency": str(item.get("dependency") or "independent"),
                    "source": "llm",
                }
            )
        return tasks

    def _split_with_rules(self, user_text: str) -> list[dict[str, Any]]:
        clauses = self._split_clauses(user_text)
        tasks: list[dict[str, Any]] = []
        current_day = ""
        for clause in clauses:
            if not clause:
                continue
            explicit_day = self._first_day_token(clause)
            day_scope = explicit_day or current_day
            if explicit_day:
                current_day = explicit_day
            for part in self._split_internal_items(clause):
                text = part.strip("，。；; ")
                if not text:
                    continue
                if day_scope and day_scope not in text:
                    text = f"{day_scope}{text}"
                tasks.append(
                    {
                        "id": f"sub_{len(tasks) + 1}",
                        "text": text,
                        "scope": {
                            "days": self._normalize_days([], text, fallback_day=day_scope),
                            "meals": self._normalize_meals([], text),
                        },
                        "dependency": "independent",
                        "source": "rules",
                    }
                )
        if not tasks:
            tasks.append(
                {
                    "id": "sub_1",
                    "text": user_text.strip(),
                    "scope": {"days": self._normalize_days([], user_text), "meals": self._normalize_meals([], user_text)},
                    "dependency": "independent",
                    "source": "rules",
                }
            )
        return tasks

    def _split_clauses(self, user_text: str) -> list[str]:
        parts = re.split(
            r"(?:另外|还有|并且|同时|以及|然后|再加上| and |[；;]\s*|\n|(?=第[一二三四五六七]天))",
            user_text,
            flags=re.IGNORECASE,
        )
        cleaned = [part.strip("，。；; ") for part in parts if part.strip("，。；; ")]
        return cleaned or [user_text.strip()]

    def _split_internal_items(self, clause: str) -> list[str]:
        if "，" in clause and sum(token in clause for token in self.DAY_ORDER) <= 1:
            parts = [item.strip() for item in clause.split("，") if item.strip()]
            if len(parts) > 1:
                return parts
        return [clause]

    def _first_day_token(self, text: str) -> str:
        for token in self.DAY_ORDER:
            if token in text:
                return token
        match = re.search(r"day\s*([1-7])", text, flags=re.IGNORECASE)
        return f"day{match.group(1)}" if match else ""

    def _valid_task_text(self, text: str, user_text: str) -> bool:
        if not text:
            return False
        if any(marker in text for marker in ["乱码", "无有效需求", "无法识别", "不可解析"]):
            return False
        if self._contains_cjk(user_text) and not self._contains_cjk(text):
            return False
        return True

    def _normalize_days(self, value: object, text: str, fallback_day: str = "") -> list[str]:
        result: list[str] = []
        if isinstance(value, list):
            for item in value:
                normalized = str(item).strip().lower()
                if normalized.startswith("day") and normalized not in result:
                    result.append(normalized)
        for token, normalized in self.DAY_ORDER.items():
            if token in text and normalized not in result:
                result.append(normalized)
        if fallback_day:
            normalized = self.DAY_ORDER.get(fallback_day, fallback_day.lower())
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def _normalize_meals(self, value: object, text: str) -> list[str]:
        result: list[str] = []
        if isinstance(value, list):
            for item in value:
                normalized = str(item).strip().lower()
                if normalized in {"breakfast", "lunch", "dinner", "late_night"} and normalized not in result:
                    result.append(normalized)
        for token, normalized in self.MEAL_KEYWORDS.items():
            if token in text and normalized not in result:
                result.append(normalized)
        return result

    def _contains_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))
