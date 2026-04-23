from __future__ import annotations

import re

from ..models import TravelConstraints, TripRequest


class RequirementAgent:
    _DAY_CLAUSE_PATTERN = re.compile(
        r"(?:第\s*([一二三四五六七八九十百零\d]+)\s*天|day\s*([1-9]\d*))([^。；;\n]*)",
        flags=re.IGNORECASE,
    )
    _TOKEN_SPLIT_PATTERN = re.compile(r"[、，,;；/|]+")
    _PUNCT_STRIP = " ，。；;、!！?？:：()（）[]【】\"' "

    def build_constraints(self, request: TripRequest, llm_provider: object | None = None) -> tuple[TravelConstraints, str]:
        must_include_spots = self._extract_required_spots_from_preferred_areas(request.preferred_areas)
        must_include_spots_by_day = self._extract_day_arrangement(
            notes=request.additional_notes,
            required_spots=must_include_spots,
            max_days=request.days,
        )
        request_text = (
            f"{request.origin} -> {request.destination}, {request.days} days, budget {request.budget:.0f}. "
            f"interests={request.interests}, style={request.style}, preferred_areas={request.preferred_areas}, "
            f"avoid_tags={request.avoid_tags}, must_have_hotel_area={request.must_have_hotel_area or ''}, "
            f"additional_notes={request.additional_notes or ''}"
        )

        if llm_provider and hasattr(llm_provider, "generate_constraints"):
            generated = llm_provider.generate_constraints(
                request_text,
                {
                    "destination": request.destination,
                    "days": request.days,
                    "budget": request.budget,
                    "interests": request.interests,
                    "preferred_areas": request.preferred_areas,
                    "avoid_tags": request.avoid_tags,
                    "style": request.style,
                    "must_have_hotel_area": request.must_have_hotel_area,
                    "additional_notes": request.additional_notes,
                },
            )
            if generated:
                min_spots = {"relaxed": 4, "balanced": 5, "dense": 7}[request.style]
                min_transfers = {"relaxed": 4, "balanced": 5, "dense": 7}[request.style]
                generated.max_daily_spots = max(min_spots, int(generated.max_daily_spots))
                generated.max_daily_transport_transfers = max(min_transfers, int(generated.max_daily_transport_transfers))
                generated.max_total_budget = max(float(generated.max_total_budget), float(request.budget))
                generated.preferred_areas = self._merge_unique(generated.preferred_areas, request.preferred_areas)
                generated.must_have_tags = self._merge_unique(generated.must_have_tags, request.interests)
                generated.avoid_tags = self._merge_unique(generated.avoid_tags, request.avoid_tags)
                generated.must_include_spots = self._merge_unique(generated.must_include_spots, must_include_spots)
                generated.must_include_spots_by_day = self._merge_day_spots(
                    generated.must_include_spots_by_day,
                    must_include_spots_by_day,
                )
                if not generated.pacing_note:
                    generated.pacing_note = f"style={request.style}, relaxed hard limits, prioritize user preferences."
                return generated, "LLM constraints merged with preferred_areas must-hit and day arrangement from notes"

        constraints = TravelConstraints(
            max_daily_spots={"relaxed": 4, "balanced": 5, "dense": 7}[request.style],
            max_daily_transport_transfers={"relaxed": 4, "balanced": 5, "dense": 7}[request.style],
            max_total_budget=request.budget,
            preferred_areas=list(request.preferred_areas),
            must_have_tags=list(request.interests),
            avoid_tags=list(request.avoid_tags),
            must_include_spots=must_include_spots,
            must_include_spots_by_day=must_include_spots_by_day,
            pacing_note=f"style={request.style}, preference-first and best-effort fulfillment.",
        )
        return constraints, "Rule constraints built from form fields (preferred_areas => must-hit; notes => day arrangement)"

    def _extract_required_spots_from_preferred_areas(self, preferred_areas: list[str]) -> list[str]:
        required: list[str] = []
        for raw_item in preferred_areas:
            if not raw_item:
                continue
            tokens = self._split_spot_candidates(raw_item)
            required = self._merge_unique(required, tokens)
        return required

    def _extract_day_arrangement(self, notes: str, required_spots: list[str], max_days: int) -> dict[int, list[str]]:
        if not notes or not required_spots:
            return {}

        arranged: dict[int, list[str]] = {}
        for match in self._DAY_CLAUSE_PATTERN.finditer(notes):
            day_token = match.group(1) or match.group(2) or ""
            day_value = self._to_day_number(day_token)
            if day_value <= 0 or day_value > max(max_days, 1):
                continue
            clause = match.group(0)
            for spot in required_spots:
                if self._contains_spot_name(clause, spot):
                    arranged[day_value] = self._merge_unique(arranged.get(day_value, []), [spot])
        return arranged

    def _split_spot_candidates(self, raw: str) -> list[str]:
        cleaned = (raw or "").strip()
        if not cleaned:
            return []
        parts = [item.strip(self._PUNCT_STRIP) for item in self._TOKEN_SPLIT_PATTERN.split(cleaned)]
        output: list[str] = []
        for part in parts:
            token = self._normalize_spot_token(part)
            if len(token) <= 1:
                continue
            output = self._merge_unique(output, [token])
        return output

    def _normalize_spot_token(self, token: str) -> str:
        value = token.strip(self._PUNCT_STRIP)
        value = re.sub(r"^(安排|游览|打卡|前往|去|到)\s*", "", value)
        value = re.sub(r"\s*(景点|区域|片区|附近)$", "", value)
        return value.strip(self._PUNCT_STRIP)

    def _contains_spot_name(self, sentence: str, spot_name: str) -> bool:
        sentence_key = self._normalize_text(sentence)
        spot_key = self._normalize_text(spot_name)
        return bool(sentence_key and spot_key and (spot_key in sentence_key or sentence_key in spot_key))

    def _to_day_number(self, raw: str) -> int:
        token = (raw or "").strip().lower()
        if token.isdigit():
            return int(token)

        chinese_digits = {
            "零": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if token in chinese_digits:
            return chinese_digits[token]
        if token == "十":
            return 10
        if token.startswith("十") and len(token) == 2 and token[1] in chinese_digits:
            return 10 + chinese_digits[token[1]]
        if token.endswith("十") and len(token) == 2 and token[0] in chinese_digits:
            return chinese_digits[token[0]] * 10
        if "十" in token:
            left, right = token.split("十", 1)
            left_value = chinese_digits.get(left, 1) if left else 1
            right_value = chinese_digits.get(right, 0) if right else 0
            return left_value * 10 + right_value
        return 0

    def _normalize_text(self, value: str) -> str:
        lowered = value.strip().lower()
        return re.sub(r"[\s·・,，。；;:：\-_/()（）\[\]【】]+", "", lowered)

    def _merge_unique(self, base: list[str], incoming: list[str]) -> list[str]:
        seen = {self._normalize_text(item) for item in base if item.strip()}
        merged = [item.strip() for item in base if item.strip()]
        for item in incoming:
            normalized = item.strip()
            if not normalized:
                continue
            key = self._normalize_text(normalized)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
        return merged

    def _merge_day_spots(self, base: dict[int, list[str]], incoming: dict[int, list[str]]) -> dict[int, list[str]]:
        merged = {int(day): self._merge_unique([], names) for day, names in base.items()}
        for day, names in incoming.items():
            day_key = int(day)
            merged[day_key] = self._merge_unique(merged.get(day_key, []), names)
        return merged
