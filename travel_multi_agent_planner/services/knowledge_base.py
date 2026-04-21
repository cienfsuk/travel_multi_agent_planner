from __future__ import annotations

from ..data.sample_city_data import SAMPLE_CITY_DATA
from ..models import CityProfile, PointOfInterest, TripRequest


class LocalTravelKnowledgeBase:
    def __init__(self) -> None:
        self._profiles = SAMPLE_CITY_DATA

    def supported_destinations(self) -> list[str]:
        return sorted(profile.city for profile in self._profiles)

    def get_city_profile(self, destination: str) -> CityProfile:
        normalized = destination.strip().lower()
        for profile in self._profiles:
            alias_set = {profile.city.lower(), *[alias.lower() for alias in profile.aliases]}
            if normalized in alias_set:
                return profile
        supported = ", ".join(self.supported_destinations())
        raise ValueError(f"Unsupported destination '{destination}'. Supported destinations: {supported}")

    def find_city_profile(self, destination: str) -> CityProfile | None:
        normalized = destination.strip().lower()
        for profile in self._profiles:
            alias_set = {profile.city.lower(), *[alias.lower() for alias in profile.aliases]}
            if normalized in alias_set:
                return profile
        return None

    def rank_pois(self, profile: CityProfile, request: TripRequest) -> list[PointOfInterest]:
        interests = set(request.interests)

        def score(poi: PointOfInterest) -> tuple[int, float]:
            tag_hits = len(interests.intersection(poi.tags))
            budget_bonus = 1.0 if poi.ticket_cost <= max(request.budget / max(request.days, 1) / 4, 40.0) else 0.0
            free_bonus = 0.5 if poi.ticket_cost == 0 else 0.0
            return (tag_hits, budget_bonus + free_bonus)

        return sorted(profile.pois, key=score, reverse=True)
