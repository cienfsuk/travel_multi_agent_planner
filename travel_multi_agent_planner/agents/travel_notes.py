from __future__ import annotations

from ..models import CityProfile, TravelNote, TripRequest


class TravelNotesAgent:
    def collect(
        self,
        request: TripRequest,
        profile: CityProfile,
        search_provider: object,
        llm_provider: object | None = None,
    ) -> tuple[list[TravelNote], list[str]]:
        if hasattr(search_provider, "search_travel_notes"):
            notes = search_provider.search_travel_notes(request, profile, llm_provider)
            return notes, [f"已基于 {profile.city} 的真实在线点位生成攻略摘要。"] if notes else [f"{profile.city} 暂未获得攻略摘要。"]
        return [], [f"{profile.city} 未找到可用的攻略聚合 provider。"]
