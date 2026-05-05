from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

import requests

from ..models import CityProfile, FoodVenue, HotelVenue, PointOfInterest, TravelConstraints, TripRequest, ValidationIssue


class BailianLLMProvider:
    def __init__(self, api_key: str | None, model: str = "qwen-plus", timeout: int = 35) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def generate(self, prompt: str, system_prompt: str | None = None, temperature: float = 0.3) -> str | None:
        """Backward-compatible async text generation wrapper."""
        return self.generate_text(system_prompt or "You are a helpful assistant.", prompt, temperature=temperature)

    def generate_text(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str | None:
        """Unified text generation entrypoint for personalization agents."""
        return self._chat_text(system_prompt=system_prompt, user_prompt=user_prompt, temperature=temperature)

    def generate_json(
        self,
        system_prompt: str,
        user_payload: object,
        schema_hint: str = "",
        temperature: float = 0.1,
    ) -> dict | list | None:
        """Unified JSON generation entrypoint for personalization agents."""
        payload = user_payload if isinstance(user_payload, str) else json.dumps(user_payload, ensure_ascii=False)
        prompt = payload if not schema_hint else f"{payload}\n\nJSON_SCHEMA_HINT:\n{schema_hint}"
        return self._chat_json(system_prompt=system_prompt, user_prompt=prompt, temperature=temperature)

    def generate_code(self, system_prompt: str, user_payload: object, temperature: float = 0.2) -> str | None:
        """Unified code generation entrypoint for personalization agents."""
        payload = user_payload if isinstance(user_payload, str) else json.dumps(user_payload, ensure_ascii=False)
        return self._chat_text(system_prompt=system_prompt, user_prompt=payload, temperature=temperature)

    def generate_repair(self, system_prompt: str, user_payload: object, temperature: float = 0.15) -> str | None:
        """Unified repair generation entrypoint for personalization agents."""
        payload = user_payload if isinstance(user_payload, str) else json.dumps(user_payload, ensure_ascii=False)
        return self._chat_text(system_prompt=system_prompt, user_prompt=payload, temperature=temperature)

    def generate_constraints(self, request_text: str, user_form: dict) -> TravelConstraints | None:
        if not self.is_available():
            return None
        prompt = (
            "You are a travel planning constraint extractor. "
            "Return only JSON with keys: max_daily_spots, max_daily_transport_transfers, "
            "max_total_budget, preferred_areas, must_have_tags, avoid_tags, "
            "must_include_spots, must_include_spots_by_day, pacing_note."
        )
        result = self._chat_json(
            system_prompt=prompt,
            user_prompt=json.dumps({"request_text": request_text, "user_form": user_form}, ensure_ascii=False),
        )
        if not result:
            return None
        try:
            return TravelConstraints(
                max_daily_spots=int(result["max_daily_spots"]),
                max_daily_transport_transfers=int(result["max_daily_transport_transfers"]),
                max_total_budget=float(result["max_total_budget"]),
                preferred_areas=list(result.get("preferred_areas", [])),
                must_have_tags=list(result.get("must_have_tags", [])),
                avoid_tags=list(result.get("avoid_tags", [])),
                must_include_spots=list(result.get("must_include_spots", [])),
                must_include_spots_by_day={
                    int(day): [str(name) for name in names]
                    for day, names in (result.get("must_include_spots_by_day", {}) or {}).items()
                    if isinstance(names, list)
                },
                pacing_note=str(result.get("pacing_note", "")),
            )
        except Exception:
            return None

    def summarize_candidates(self, search_results: list[dict]) -> list[dict] | None:
        if not self.is_available():
            return None
        prompt = (
            "You are summarizing travel candidates. Return only JSON array. "
            "Each item must contain name, relevance_reason, travel_value_score."
        )
        result = self._chat_json(system_prompt=prompt, user_prompt=json.dumps(search_results, ensure_ascii=False))
        return result if isinstance(result, list) else None

    def draft_itinerary(
        self,
        request: TripRequest,
        candidates: list[PointOfInterest],
        constraints: TravelConstraints,
    ) -> list[dict] | None:
        if not self.is_available():
            return None
        candidate_payload = [
            {
                "name": poi.name,
                "district": poi.district,
                "category": poi.category,
                "ticket_cost": poi.ticket_cost,
                "duration_hours": poi.duration_hours,
                "tags": poi.tags,
                "best_time": poi.best_time,
            }
            for poi in candidates
        ]
        prompt = (
            "You are planning a city deep-trip. Return only JSON array. "
            "Each array item must include day, theme, spot_names, notes. "
            "Only use names from the provided candidates."
        )
        result = self._chat_json(
            system_prompt=prompt,
            user_prompt=json.dumps(
                {
                    "request": self._to_jsonable(request),
                    "constraints": self._to_jsonable(constraints),
                    "candidates": candidate_payload,
                },
                ensure_ascii=False,
            ),
        )
        return result if isinstance(result, list) else None

    def revise_itinerary(
        self,
        draft_plan: list[dict],
        validation_issues: list[ValidationIssue],
        candidates: list[PointOfInterest],
    ) -> list[dict] | None:
        if not self.is_available():
            return None
        prompt = (
            "You are revising a travel itinerary to resolve validation issues. "
            "Return only JSON array with day, theme, spot_names, notes. "
            "Only use candidate names."
        )
        result = self._chat_json(
            system_prompt=prompt,
            user_prompt=json.dumps(
                {
                    "draft_plan": self._to_jsonable(draft_plan),
                    "issues": self._to_jsonable(validation_issues),
                    "candidates": [poi.name for poi in candidates],
                },
                ensure_ascii=False,
            ),
        )
        return result if isinstance(result, list) else None

    def select_hotel(
        self,
        request: TripRequest,
        hotel_candidates: list[HotelVenue],
        day_spots: list[PointOfInterest],
    ) -> dict | None:
        if not self.is_available():
            return None
        prompt = (
            "You are selecting a hotel for a travel day. Return only JSON with keys: hotel_name, reason. "
            "hotel_name must exactly match one provided candidate."
        )
        result = self._chat_json(
            system_prompt=prompt,
            user_prompt=json.dumps(
                {
                    "request": self._to_jsonable(request),
                    "hotels": self._to_jsonable(hotel_candidates),
                    "spots": self._to_jsonable(day_spots),
                },
                ensure_ascii=False,
            ),
        )
        return result if isinstance(result, dict) else None

    def compose_itinerary_with_notes(
        self,
        request: TripRequest,
        draft_plan: list[dict],
        travel_notes: list[dict],
    ) -> dict | None:
        if not self.is_available():
            return None
        prompt = (
            "You are refining a travel itinerary with travel notes. "
            "Return only JSON with keys: summary, highlights, caution."
        )
        result = self._chat_json(
            system_prompt=prompt,
            user_prompt=json.dumps(
                {
                    "request": self._to_jsonable(request),
                    "draft_plan": self._to_jsonable(draft_plan),
                    "travel_notes": self._to_jsonable(travel_notes),
                },
                ensure_ascii=False,
            ),
        )
        return result if isinstance(result, dict) else None

    def write_guide(self, final_plan: dict) -> str | None:
        if not self.is_available():
            return None
        prompt = (
            "You are writing a concise but polished travel guide in Chinese markdown. "
            "Use headings, bullet points, and a practical tone."
        )
        return self._chat_text(system_prompt=prompt, user_prompt=json.dumps(final_plan, ensure_ascii=False))

    def generate_city_profile(self, city_name: str) -> CityProfile | None:
        if not self.is_available():
            return None
        prompt = (
            "You are generating a city travel profile for a Chinese UI app. "
            "Return only JSON with keys: intro, local_transport_tip, recommended_seasons, pois, foods. "
            "pois is an array of 5 items, each with name, category, district, description, duration_hours, "
            "ticket_cost, lat, lon, tags, best_time. "
            "foods is an array of 4 items, each with name, district, cuisine, description, average_cost, tags, taste_profile."
        )
        result = self._chat_json(system_prompt=prompt, user_prompt=city_name)
        if not isinstance(result, dict):
            return None
        try:
            pois = [
                PointOfInterest(
                    name=str(item["name"]),
                    category=str(item["category"]),
                    district=str(item["district"]),
                    description=str(item["description"]),
                    duration_hours=float(item["duration_hours"]),
                    ticket_cost=float(item["ticket_cost"]),
                    lat=float(item["lat"]),
                    lon=float(item["lon"]),
                    tags=[str(tag) for tag in item.get("tags", [])],
                    best_time=str(item.get("best_time", "afternoon")),
                )
                for item in result.get("pois", [])[:5]
            ]
            foods = [
                FoodVenue(
                    name=str(item["name"]),
                    district=str(item["district"]),
                    cuisine=str(item["cuisine"]),
                    description=str(item["description"]),
                    average_cost=float(item["average_cost"]),
                    tags=[str(tag) for tag in item.get("tags", [])],
                    taste_profile=[str(tag) for tag in item.get("taste_profile", [])],
                )
                for item in result.get("foods", [])[:4]
            ]
            if not pois or not foods:
                return None
            return CityProfile(
                city=city_name,
                aliases=[city_name.lower()],
                intro=str(result.get("intro", f"{city_name} 适合城市深度游。")),
                local_transport_tip=str(result.get("local_transport_tip", "优先使用地铁、公交与步行组合。")),
                daily_local_transport_cost=28.0,
                accommodation_budget={"budget": 180.0, "balanced": 320.0, "premium": 520.0},
                intercity_transport={},
                recommended_seasons=[str(item) for item in result.get("recommended_seasons", ["spring", "autumn"])],
                pois=pois,
                foods=foods,
            )
        except Exception:
            return None

    def _chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> dict | list | None:
        text = self._chat_text(system_prompt, user_prompt, temperature=temperature)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return None

    def _chat_text(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str | None:
        if not self.api_key:
            return None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        try:
            response = requests.post(self.base_url, headers=headers, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    def _to_jsonable(self, value: object) -> object:
        if is_dataclass(value):
            return {key: self._to_jsonable(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): self._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._to_jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [self._to_jsonable(item) for item in value]
        return value

    @staticmethod
    def timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
