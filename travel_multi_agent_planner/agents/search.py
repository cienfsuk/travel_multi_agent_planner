from __future__ import annotations

import math

from ..models import CityMatch, CityProfile, PointOfInterest, TravelConstraints, TripRequest


class SearchAgent:
    def build_city_profile(
        self,
        request: TripRequest,
        search_provider: object,
    ) -> tuple[CityProfile, CityMatch, CityMatch, list[str]]:
        if not hasattr(search_provider, "confirm_city") or not hasattr(search_provider, "build_city_profile"):
            raise RuntimeError("当前搜索 provider 不支持腾讯城市确认与在线资料构建。")
        origin_match = search_provider.confirm_city(request.origin)
        destination_match = search_provider.confirm_city(request.destination)
        profile, notes = search_provider.build_city_profile(destination_match, request.food_tastes)
        return profile, origin_match, destination_match, notes

    def rank_pois(
        self,
        pois: list[PointOfInterest],
        request: TripRequest,
        constraints: TravelConstraints,
    ) -> list[PointOfInterest]:
        deduped = self._dedupe_pois(pois)
        request_interests = set(request.interests)
        preferred_areas = {area.lower() for area in constraints.preferred_areas}
        avoid_tags = set(constraints.avoid_tags)

        def score(poi: PointOfInterest) -> tuple[float, float, float]:
            tag_hits = len(request_interests.intersection(poi.tags))
            evidence_bonus = len(poi.source_evidence) * 0.5
            area_text = f"{poi.district} {poi.address} {poi.description}".lower()
            area_bonus = 1.0 if any(area and area in area_text for area in preferred_areas) else 0.0
            avoid_penalty = -2.0 if avoid_tags.intersection(poi.tags) else 0.0
            affordability = 0.8 if poi.ticket_cost <= max(request.budget / max(request.days, 1) / 3, 60.0) else 0.0
            locality_bonus = self._locality_bonus(poi, deduped)
            district_bonus = self._district_density_bonus(poi, deduped)
            return (
                tag_hits + evidence_bonus + area_bonus + avoid_penalty + affordability + locality_bonus + district_bonus,
                -poi.ticket_cost,
                poi.duration_hours,
            )

        return sorted(deduped, key=score, reverse=True)

    def _dedupe_pois(self, pois: list[PointOfInterest]) -> list[PointOfInterest]:
        deduped: list[PointOfInterest] = []
        seen_keys: set[str] = set()
        for poi in pois:
            key = self._poi_key(poi)
            if key in seen_keys:
                continue
            if any(self._is_near_duplicate(poi, existing) for existing in deduped):
                continue
            seen_keys.add(key)
            deduped.append(poi)
        return deduped

    def _poi_key(self, poi: PointOfInterest) -> str:
        address = (poi.address or poi.description or poi.district).strip().lower()
        return f"{poi.name.strip().lower()}|{address}"

    def _is_near_duplicate(self, left: PointOfInterest, right: PointOfInterest) -> bool:
        left_name = left.name.strip().lower()
        right_name = right.name.strip().lower()
        if left_name == right_name:
            return True
        if left.address and right.address and left.address.strip().lower() == right.address.strip().lower():
            return True
        if self._same_name_core(left_name, right_name) and self._haversine(left.lat, left.lon, right.lat, right.lon) <= 0.35:
            return True
        return False

    def _same_name_core(self, left_name: str, right_name: str) -> bool:
        left_core = left_name.replace("景区", "").replace("风景区", "").replace("博物馆", "").replace("旅游区", "").strip()
        right_core = right_name.replace("景区", "").replace("风景区", "").replace("博物馆", "").replace("旅游区", "").strip()
        return left_core == right_core and bool(left_core)

    def _locality_bonus(self, poi: PointOfInterest, candidates: list[PointOfInterest]) -> float:
        close_neighbors = 0
        for candidate in candidates:
            if candidate.name == poi.name:
                continue
            distance = self._haversine(poi.lat, poi.lon, candidate.lat, candidate.lon)
            if distance <= 2.0:
                close_neighbors += 1
            elif distance <= 5.0:
                close_neighbors += 0.4
        return min(2.4, close_neighbors * 0.6)

    def _district_density_bonus(self, poi: PointOfInterest, candidates: list[PointOfInterest]) -> float:
        district = poi.district.strip().lower()
        if not district:
            return 0.0
        matches = sum(1 for candidate in candidates if candidate.district.strip().lower() == district)
        return min(1.4, max(0, matches - 1) * 0.35)

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
        )
        return 2 * radius * math.asin(math.sqrt(a))
