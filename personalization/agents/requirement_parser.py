"""Requirement Parser Agent - converts natural language into structured personalization intent."""

from __future__ import annotations

import re
from typing import Any

from ..models import ModificationType, ParsedRequirement


DOMAIN_FILE_MAP = {
    "food_spot": "travel_multi_agent_planner/agents/food_spot.py",
    "transport": "travel_multi_agent_planner/agents/transport.py",
    "planner": "travel_multi_agent_planner/agents/planner.py",
    "hotel": "travel_multi_agent_planner/agents/hotel.py",
    "budget": "travel_multi_agent_planner/agents/budget.py",
    "search": "travel_multi_agent_planner/agents/search.py",
    "requirement": "travel_multi_agent_planner/agents/requirement.py",
    "validator": "travel_multi_agent_planner/agents/validator.py",
}

DOMAIN_KEYWORDS = {
    "food_spot": ["吃", "餐", "饭", "早餐", "早饭", "午饭", "午餐", "中午", "晚饭", "晚餐", "晚上", "夜宵", "火锅", "烧烤", "日料", "韩料", "面", "咖啡", "甜品", "food", "meal", "restaurant"],
    "transport": ["交通", "地铁", "公交", "打车", "停车", "自驾", "开车", "高铁", "火车", "班次", "出发", "返程", "步行", "骑行", "transport", "train", "drive", "metro", "bus"],
    "planner": ["行程", "景点", "安排", "规划", "轻松", "紧凑", "节奏", "分散", "集中", "plan", "spot", "relaxed", "dense"],
    "hotel": ["酒店", "住宿", "民宿", "住", "地铁站旁", "hotel", "stay", "accommodation"],
    "budget": ["预算", "省钱", "便宜", "高端", "豪华", "budget", "cost", "price"],
    "search": ["小众", "热门", "隐藏", "搜索", "排名", "search", "rank", "popular"],
    "requirement": ["约束", "必须", "禁止", "规则", "constraint", "requirement"],
    "validator": ["校验", "验证", "检查", "validate", "validator"],
}


class RequirementParserAgent:
    """Parse personalization requests into structured CODE operations."""

    def __init__(self, llm_provider: Any = None):
        self.llm = llm_provider

    def parse(self, user_text: str, context: dict | None = None) -> ParsedRequirement:
        rule_domains = self._detect_domains(user_text)
        feature_type = self._detect_feature_type(user_text)
        rule_parameters = self._heuristic_structure(user_text, rule_domains, feature_type)
        llm_parsed = self.parse_with_llm(user_text, context)
        if llm_parsed is not None:
            merged_domains = list(dict.fromkeys(rule_domains + list(llm_parsed.parameters.get("domains", []))))
            llm_constraints = llm_parsed.parameters.get("constraints", {})
            rule_constraints = rule_parameters.get("constraints", {})
            merged_constraints = {
                key: list(dict.fromkeys(list(rule_constraints.get(key, [])) + list(llm_constraints.get(key, []))))
                for key in {
                    "days",
                    "meals",
                    "transport",
                    "hotel",
                    "hard_constraints",
                    "avoidances",
                    "preferences",
                    "keywords",
                }
            }
            llm_parsed.target_files = [DOMAIN_FILE_MAP[domain] for domain in merged_domains]
            llm_parsed.parameters = {
                **rule_parameters,
                **llm_parsed.parameters,
                "domains": merged_domains,
                "constraints": merged_constraints,
            }
            return llm_parsed

        return ParsedRequirement(
            raw_text=user_text,
            target_files=[DOMAIN_FILE_MAP[domain] for domain in rule_domains],
            modification_type=ModificationType.CODE,
            parameters=rule_parameters,
            confidence=0.78,
        )

    def parse_with_llm(self, user_text: str, context: dict | None = None) -> ParsedRequirement | None:
        if not self.llm or not hasattr(self.llm, "generate_json"):
            return None

        schema_hint = """
{
  "domains": ["planner"],
  "feature_type": "prefer",
  "intent_summary": "用中文概括用户诉求",
  "constraints": {
    "days": ["day1"],
    "meals": ["dinner"],
    "transport": [],
    "hotel": [],
    "hard_constraints": [],
    "avoidances": [],
    "preferences": [],
    "keywords": []
  }
}
""".strip()
        system_prompt = (
            "You parse a Chinese travel personalization request into JSON. "
            "Choose domains only from planner, food_spot, transport, hotel, budget, search, requirement, validator. "
            "If the request mentions meal or cuisine preferences, include food_spot. "
            "If it mentions departure time, train, transit, parking, or driving, include transport. "
            "Return JSON only. Do not claim the user input is garbled unless the text is actually unreadable."
        )
        result = self.llm.generate_json(
            system_prompt,
            {"user_text": user_text, "context": context or {}},
            schema_hint=schema_hint,
            temperature=0.1,
        )
        if not self._valid_llm_result(result, user_text):
            return None

        assert isinstance(result, dict)
        domains = self._normalize_domains(result.get("domains")) or self._detect_domains(user_text)
        feature_type = str(result.get("feature_type") or self._detect_feature_type(user_text)).strip() or "custom"
        constraints = result.get("constraints") if isinstance(result.get("constraints"), dict) else {}
        parameters = {
            "feature_type": feature_type,
            "domains": domains,
            "intent_summary": str(result.get("intent_summary") or user_text).strip(),
            "constraints": {
                "days": self._normalize_string_list(constraints.get("days")),
                "meals": self._normalize_string_list(constraints.get("meals")),
                "transport": self._normalize_string_list(constraints.get("transport")),
                "hotel": self._normalize_string_list(constraints.get("hotel")),
                "hard_constraints": self._normalize_string_list(constraints.get("hard_constraints")),
                "avoidances": self._normalize_string_list(constraints.get("avoidances")),
                "preferences": self._normalize_string_list(constraints.get("preferences")),
                "keywords": self._normalize_string_list(constraints.get("keywords")),
            },
            "llm_used": True,
        }
        return ParsedRequirement(
            raw_text=user_text,
            target_files=[DOMAIN_FILE_MAP[domain] for domain in domains],
            modification_type=ModificationType.CODE,
            parameters=parameters,
            confidence=0.9,
        )

    def _heuristic_structure(self, user_text: str, domains: list[str], feature_type: str) -> dict[str, Any]:
        lowered = user_text.lower()
        preferences: list[str] = []
        if "火锅" in user_text:
            preferences.append("火锅")
        if "烧烤" in user_text:
            preferences.append("烧烤")
        if "日料" in user_text:
            preferences.append("日料")
        if "韩料" in user_text:
            preferences.append("韩料")
        if "面" in user_text:
            preferences.append("面食")
        if "轻松" in user_text or "relaxed" in lowered:
            preferences.append("轻松节奏")
        if any(token in user_text for token in ["别太早", "不要太早", "太早", "晚点出发", "晚些出发"]) or "early" in lowered:
            preferences.append("避免过早出发")

        avoidances: list[str] = []
        if any(token in user_text for token in ["不要", "别", "避免", "禁止"]) or "avoid" in lowered:
            avoidances.append("negative_preference")

        return {
            "feature_type": feature_type,
            "domains": domains,
            "intent_summary": user_text.strip(),
            "constraints": {
                "days": self._extract_days(user_text),
                "meals": self._extract_meals(user_text),
                "transport": self._extract_present_items(user_text, ["地铁", "公交", "打车", "开车", "自驾", "停车", "高铁", "火车"]),
                "hotel": self._extract_present_items(user_text, ["酒店", "住宿", "民宿", "地铁站", "车站"]),
                "hard_constraints": [],
                "avoidances": avoidances,
                "preferences": preferences,
                "keywords": [token for token in ["relaxed", "early", "hotel", "metro"] if token in lowered],
            },
            "llm_used": False,
        }

    def _extract_days(self, user_text: str) -> list[str]:
        mapping = {
            "第一天": "day1",
            "第二天": "day2",
            "第三天": "day3",
            "第四天": "day4",
            "第五天": "day5",
            "第六天": "day6",
            "第七天": "day7",
        }
        result = [value for token, value in mapping.items() if token in user_text]
        if result:
            return result
        matches = re.findall(r"day\s*([1-7])", user_text, flags=re.IGNORECASE)
        return [f"day{match}" for match in matches]

    def _extract_meals(self, user_text: str) -> list[str]:
        mapping = {
            "早餐": "breakfast",
            "早饭": "breakfast",
            "午饭": "lunch",
            "午餐": "lunch",
            "中午": "lunch",
            "晚饭": "dinner",
            "晚餐": "dinner",
            "晚上": "dinner",
            "夜宵": "late_night",
            "宵夜": "late_night",
        }
        result: list[str] = []
        for token, value in mapping.items():
            if token in user_text and value not in result:
                result.append(value)
        return result

    def _extract_present_items(self, user_text: str, candidates: list[str]) -> list[str]:
        return [item for item in candidates if item in user_text]

    def _detect_domains(self, user_text: str) -> list[str]:
        lowered = user_text.lower()
        domains = [domain for domain, keywords in DOMAIN_KEYWORDS.items() if any(keyword.lower() in lowered for keyword in keywords)]
        return domains or ["planner"]

    def _detect_feature_type(self, user_text: str) -> str:
        lowered = user_text.lower()
        if any(word in user_text for word in ["不要", "跳过", "删掉"]) or any(word in lowered for word in ["skip", "remove"]):
            return "skip"
        if any(word in user_text for word in ["偏好", "喜欢", "想吃", "想要", "希望"]) or "prefer" in lowered:
            return "prefer"
        if any(word in user_text for word in ["避免", "别"]) or "avoid" in lowered:
            return "avoid"
        if any(word in user_text for word in ["修改", "改变", "调整", "设置"]) or "modify" in lowered:
            return "modify"
        if any(word in user_text for word in ["新增", "添加", "增加"]) or "add" in lowered:
            return "add"
        return "custom"

    def _valid_llm_result(self, result: object, user_text: str) -> bool:
        if not isinstance(result, dict):
            return False
        summary = str(result.get("intent_summary") or "").strip()
        if not summary:
            return False
        bad_markers = ["乱码", "无有效需求", "无法识别", "不可解析"]
        if any(marker in summary for marker in bad_markers):
            return False
        if self._contains_cjk(user_text) and not self._contains_cjk(summary):
            return False
        domains = self._normalize_domains(result.get("domains"))
        return bool(domains)

    def _contains_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _normalize_domains(self, domains: object) -> list[str]:
        if not isinstance(domains, list):
            return []
        valid = set(DOMAIN_FILE_MAP)
        result: list[str] = []
        for item in domains:
            value = str(item).strip()
            if value in valid and value not in result:
                result.append(value)
        return result

    def _normalize_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
        return result
