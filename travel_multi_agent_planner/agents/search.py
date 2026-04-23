from __future__ import annotations

import math
import re

from ..models import CityMatch, CityProfile, PointOfInterest, TravelConstraints, TripRequest


class SearchAgent:
    def build_city_profile(
        self,
        request: TripRequest,
        search_provider: object,
    ) -> tuple[CityProfile, CityMatch, CityMatch, list[str]]:
        if not hasattr(search_provider, "confirm_city") or not hasattr(search_provider, "build_city_profile"):
            raise RuntimeError("当前搜索 provider 不支持城市确认与在线资料构建。")
        origin_match = search_provider.confirm_city(request.origin)
        destination_match = search_provider.confirm_city(request.destination)
        profile, notes = search_provider.build_city_profile(destination_match, request.food_tastes)
        return profile, origin_match, destination_match, notes

    def ensure_required_spots(
        self,
        profile: CityProfile,
        constraints: TravelConstraints,
        search_provider: object,
    ) -> tuple[list[PointOfInterest], list[str]]:
        required_names = self._collect_required_spot_names(constraints)
        if not required_names:
            return self._dedupe_pois(profile.pois), []

        candidates = self._dedupe_pois(profile.pois)
        added_names: list[str] = []

        for required in required_names:
            existing_index = self._find_matching_poi_index(candidates, required)
            fetched = None
            if hasattr(search_provider, "search_poi_by_name"):
                fetched = search_provider.search_poi_by_name(profile.city, required)
            if fetched is None:
                continue
            if existing_index is None:
                candidates.append(fetched)
                added_names.append(fetched.name)
                continue
            existing = candidates[existing_index]
            if self._prefer_fetched_match(existing, fetched, required):
                candidates[existing_index] = fetched
                added_names.append(fetched.name)

        candidates = self._dedupe_pois(candidates)
        missing_names = [required for required in required_names if self._find_matching_poi(candidates, required) is None]

        hit_names: list[str] = []
        for required in required_names:
            matched = self._find_matching_poi(candidates, required)
            if matched is not None and matched.name not in hit_names:
                hit_names.append(matched.name)

        notes: list[str] = []
        if hit_names:
            notes.append(f"必达景点已命中：{'、'.join(hit_names)}。")
        if added_names:
            deduped_added = []
            seen = set()
            for name in added_names:
                key = name.strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped_added.append(name)
            if deduped_added:
                notes.append(f"已补充必达景点：{'、'.join(deduped_added)}。")
        if missing_names:
            notes.append(f"仍有必达景点未命中：{'、'.join(missing_names)}。将按现有在线数据继续规划。")
        return candidates, notes

    def rank_pois(
        self,
        pois: list[PointOfInterest],
        request: TripRequest,
        constraints: TravelConstraints,
    ) -> list[PointOfInterest]:
        deduped = self._dedupe_pois(pois)
        request_interests = set(request.interests)
        preferred_areas = {area.lower().strip() for area in constraints.preferred_areas if area.strip()}
        avoid_tags = {tag.lower().strip() for tag in constraints.avoid_tags if tag.strip()}

        if avoid_tags:
            filtered = [poi for poi in deduped if not self._has_avoid_tag(poi, avoid_tags)]
            deduped = filtered or deduped

        def score(poi: PointOfInterest) -> tuple[float, float, float]:
            tag_hits = len(request_interests.intersection(poi.tags))
            evidence_bonus = len(poi.source_evidence) * 0.5
            area_text = f"{poi.name} {poi.district} {poi.address} {poi.description}".lower()
            area_bonus = 1.2 if any(area and area in area_text for area in preferred_areas) else 0.0
            affordability = 0.8 if poi.ticket_cost <= max(request.budget / max(request.days, 1) / 3, 60.0) else 0.0
            locality_bonus = self._locality_bonus(poi, deduped)
            district_bonus = self._district_density_bonus(poi, deduped)
            return (
                tag_hits + evidence_bonus + area_bonus + affordability + locality_bonus + district_bonus,
                -poi.ticket_cost,
                poi.duration_hours,
            )

        return sorted(deduped, key=score, reverse=True)

    def _collect_required_spot_names(self, constraints: TravelConstraints) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for value in constraints.must_include_spots:
            normalized = value.strip()
            if not normalized:
                continue
            key = self._normalize_text(normalized)
            if key in seen:
                continue
            seen.add(key)
            names.append(normalized)
        for values in constraints.must_include_spots_by_day.values():
            for value in values:
                normalized = value.strip()
                if not normalized:
                    continue
                key = self._normalize_text(normalized)
                if key in seen:
                    continue
                seen.add(key)
                names.append(normalized)
        return names

    def _find_matching_poi(self, pois: list[PointOfInterest], required_name: str) -> PointOfInterest | None:
        index = self._find_matching_poi_index(pois, required_name)
        if index is None:
            return None
        return pois[index]

    def _find_matching_poi_index(self, pois: list[PointOfInterest], required_name: str) -> int | None:
        required_norm = self._normalize_text(required_name)
        if not required_norm:
            return None
        for idx, poi in enumerate(pois):
            poi_norm = self._normalize_text(poi.name)
            if not poi_norm:
                continue
            if poi_norm == required_norm or required_norm in poi_norm or poi_norm in required_norm:
                return idx
        return None

    def _prefer_fetched_match(
        self,
        existing: PointOfInterest,
        fetched: PointOfInterest,
        required_name: str,
    ) -> bool:
        if self._poi_key(existing) == self._poi_key(fetched):
            return False
        existing_score = self._required_match_score(existing.name, required_name)
        fetched_score = self._required_match_score(fetched.name, required_name)
        if fetched_score != existing_score:
            return fetched_score > existing_score

        required_norm = self._normalize_text(required_name)
        existing_gap = abs(len(self._normalize_text(existing.name)) - len(required_norm))
        fetched_gap = abs(len(self._normalize_text(fetched.name)) - len(required_norm))
        if fetched_gap != existing_gap:
            return fetched_gap < existing_gap

        # Same quality tie-break: keep Tencent top-1 query result.
        return True

    def _required_match_score(self, poi_name: str, required_name: str) -> int:
        poi_norm = self._normalize_text(poi_name)
        required_norm = self._normalize_text(required_name)
        if not poi_norm or not required_norm:
            return 0
        if poi_norm == required_norm:
            return 3
        if poi_norm.startswith(required_norm):
            return 2
        if required_norm in poi_norm or poi_norm in required_norm:
            return 1
        return 0

    def _has_avoid_tag(self, poi: PointOfInterest, avoid_tags: set[str]) -> bool:
        tags = {tag.strip().lower() for tag in poi.tags if tag.strip()}
        if tags.intersection(avoid_tags):
            return True
        text = f"{poi.name} {poi.category} {poi.description}".lower()
        return any(tag in text for tag in avoid_tags if tag)

    def _normalize_text(self, value: str) -> str:
        lowered = value.strip().lower()
        return re.sub(r"[\s·・,，。；:：\-_/()（）]+", "", lowered)

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
