from __future__ import annotations

from collections import Counter
import math
import re

from ..models import PointOfInterest, TravelConstraints, TripRequest, ValidationIssue


class PlannerAgent:
    def create_daily_spot_plan(
        self,
        request: TripRequest,
        ranked_pois: list[PointOfInterest],
        constraints: TravelConstraints,
        llm_provider: object | None = None,
    ) -> tuple[list[dict], str]:
        if llm_provider and hasattr(llm_provider, "draft_itinerary"):
            generated = llm_provider.draft_itinerary(request, ranked_pois, constraints)
            normalized = self._normalize_llm_plan(generated, ranked_pois)
            if normalized:
                enforced = self._enforce_required_spots(normalized, ranked_pois, request, constraints)
                return self._ensure_daily_density(enforced, ranked_pois, request, constraints), "百炼生成初版多日路线（含必达约束）"
        heuristic = self._heuristic_daily_spot_plan(request, ranked_pois, constraints)
        enforced = self._enforce_required_spots(heuristic, ranked_pois, request, constraints)
        return self._ensure_daily_density(enforced, ranked_pois, request, constraints), "本地规则生成初版多日路线（含必达约束）"

    def revise_daily_spot_plan(
        self,
        request: TripRequest,
        ranked_pois: list[PointOfInterest],
        constraints: TravelConstraints,
        draft_plan: list[dict],
        issues: list[ValidationIssue],
        llm_provider: object | None = None,
    ) -> tuple[list[dict], str]:
        if llm_provider and hasattr(llm_provider, "revise_itinerary"):
            revised = llm_provider.revise_itinerary(draft_plan, issues, ranked_pois)
            normalized = self._normalize_llm_plan(revised, ranked_pois)
            if normalized:
                enforced = self._enforce_required_spots(normalized, ranked_pois, request, constraints)
                return self._ensure_daily_density(enforced, ranked_pois, request, constraints), "百炼根据校验结果完成路线修正（含必达约束）"
        revised = self._heuristic_revision(request, ranked_pois, constraints)
        enforced = self._enforce_required_spots(revised, ranked_pois, request, constraints)
        return self._ensure_daily_density(enforced, ranked_pois, request, constraints), "本地规则完成路线修正（含必达约束）"

    def _heuristic_daily_spot_plan(
        self,
        request: TripRequest,
        ranked_pois: list[PointOfInterest],
        constraints: TravelConstraints,
    ) -> list[dict]:
        base_slots_per_day = {"relaxed": 2, "balanced": 3, "dense": 4}[request.style]
        selected_count = min(len(ranked_pois), max(request.days * (base_slots_per_day + 1), request.days))
        selected = ranked_pois[:selected_count]
        if not selected:
            return []
        effective_slots_per_day = max(1, min(base_slots_per_day, math.ceil(len(selected) / max(request.days, 1))))
        day_buckets = self._cluster_day_buckets(selected, request.days, effective_slots_per_day)

        daily_plans: list[dict] = []
        for day, bucket in enumerate(day_buckets, start=1):
            day_spots = bucket[:effective_slots_per_day]
            if not day_spots:
                continue
            theme = self._build_theme(day_spots)
            notes = self._build_notes(day_spots, request.style)
            daily_plans.append(
                {
                    "day": day,
                    "theme": theme,
                    "spots": day_spots,
                    "notes": notes,
                }
            )
        return daily_plans

    def _heuristic_revision(
        self,
        request: TripRequest,
        ranked_pois: list[PointOfInterest],
        constraints: TravelConstraints,
    ) -> list[dict]:
        trimmed = [poi for poi in ranked_pois if poi.ticket_cost <= max(request.budget / max(request.days, 1) / 2, 60.0)]
        if len(trimmed) < request.days:
            trimmed = ranked_pois
        trimmed = self._prefer_dense_core(trimmed, request.days, constraints.max_daily_spots)
        return self._heuristic_daily_spot_plan(request, trimmed, constraints)

    def _normalize_llm_plan(self, generated: object, ranked_pois: list[PointOfInterest]) -> list[dict]:
        if not isinstance(generated, list):
            return []
        poi_map = {poi.name.lower(): poi for poi in ranked_pois}
        normalized: list[dict] = []
        for item in generated:
            if not isinstance(item, dict):
                continue
            spot_names = item.get("spot_names", [])
            if not isinstance(spot_names, list):
                continue
            spots = [poi_map[name.lower()] for name in spot_names if isinstance(name, str) and name.lower() in poi_map]
            if not spots:
                continue
            normalized.append(
                {
                    "day": int(item.get("day", len(normalized) + 1)),
                    "theme": str(item.get("theme", self._build_theme(spots))),
                    "spots": spots,
                    "notes": self._normalize_notes(item.get("notes"), spots),
                }
            )
        return normalized

    def _normalize_notes(self, value: object, spots: list[PointOfInterest]) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        return self._build_notes(spots, "balanced")

    def _build_theme(self, spots: list[PointOfInterest]) -> str:
        category = Counter(spot.category for spot in spots).most_common(1)[0][0]
        theme_map = {
            "culture": "文化与城市地标日",
            "nature": "自然与城市漫游日",
            "food": "美食与夜游日",
            "relaxed": "慢节奏体验日",
        }
        return theme_map.get(category, "城市综合体验日")

    def _build_notes(self, spots: list[PointOfInterest], style: str) -> list[str]:
        districts = sorted({spot.district for spot in spots})
        notes = [f"当天主要集中在 {', '.join(districts)} 区域，尽量减少跨城折返。"]
        if style == "relaxed":
            notes.append("建议当天保留 1 到 2 小时自由活动时间，适合拍照和休息。")
        elif style == "dense":
            notes.append("当天节奏较紧，建议提早出发，并优先使用地铁或打车缩短通勤时间。")
        else:
            notes.append("节奏为平衡型，建议上午景点、中午休息、傍晚夜游。")
        return notes

    def _ensure_daily_density(
        self,
        daily_plans: list[dict],
        ranked_pois: list[PointOfInterest],
        request: TripRequest,
        constraints: TravelConstraints,
    ) -> list[dict]:
        if not daily_plans:
            return daily_plans
        target_per_day = {"relaxed": 2, "balanced": 3, "dense": 4}[request.style]
        if target_per_day <= 1:
            return daily_plans
        used = {spot.name.lower() for plan in daily_plans for spot in plan["spots"]}
        remaining = [poi for poi in ranked_pois if poi.name.lower() not in used]
        if not remaining:
            return daily_plans

        changed = False
        for plan in daily_plans:
            while len(plan["spots"]) < target_per_day and remaining:
                candidate = self._pick_closest_candidate(plan["spots"], remaining)
                plan["spots"].append(candidate)
                remaining = [poi for poi in remaining if poi.name.lower() != candidate.name.lower()]
                changed = True
            if changed:
                plan["theme"] = self._build_theme(plan["spots"])
                plan["notes"] = self._build_notes(plan["spots"], request.style)
        return daily_plans

    def _enforce_required_spots(
        self,
        daily_plans: list[dict],
        ranked_pois: list[PointOfInterest],
        request: TripRequest,
        constraints: TravelConstraints,
    ) -> list[dict]:
        required_names = self._required_spot_names(constraints)
        if not required_names:
            return daily_plans

        required_pois: dict[str, PointOfInterest] = {}
        missing_names: list[str] = []
        for name in required_names:
            matched = self._find_poi_for_required_name(name, ranked_pois)
            if matched is None:
                missing_names.append(name)
                continue
            required_pois[name] = matched

        normalized_plans = [dict(plan) for plan in daily_plans]
        for plan in normalized_plans:
            plan["spots"] = list(plan.get("spots", []))
            plan["notes"] = list(plan.get("notes", []))

        day_index = {int(plan["day"]): idx for idx, plan in enumerate(normalized_plans)}
        for day in range(1, request.days + 1):
            if day in day_index:
                continue
            normalized_plans.append(
                {
                    "day": day,
                    "theme": f"第 {day} 天城市探索",
                    "spots": [],
                    "notes": ["预留机动安排。"],
                }
            )
            day_index[day] = len(normalized_plans) - 1
        normalized_plans.sort(key=lambda plan: int(plan["day"]))
        day_index = {int(plan["day"]): idx for idx, plan in enumerate(normalized_plans)}

        owner: dict[str, int] = {}
        for idx, plan in enumerate(normalized_plans):
            deduped_spots: list[PointOfInterest] = []
            for spot in plan["spots"]:
                key = spot.name.strip().lower()
                if key in owner:
                    continue
                owner[key] = idx
                deduped_spots.append(spot)
            plan["spots"] = deduped_spots

        by_day_required = {int(day): list(names) for day, names in constraints.must_include_spots_by_day.items()}

        for day, names in by_day_required.items():
            if day not in day_index:
                continue
            target_idx = day_index[day]
            target_plan = normalized_plans[target_idx]
            for name in names:
                required_poi = required_pois.get(name)
                if required_poi is None:
                    continue
                key = required_poi.name.strip().lower()
                owner_idx = owner.get(key)
                if owner_idx is not None and owner_idx != target_idx:
                    source_spots = normalized_plans[owner_idx]["spots"]
                    normalized_plans[owner_idx]["spots"] = [spot for spot in source_spots if spot.name.strip().lower() != key]
                if all(spot.name.strip().lower() != key for spot in target_plan["spots"]):
                    target_plan["spots"].append(required_poi)
                owner[key] = target_idx

        for name in required_names:
            required_poi = required_pois.get(name)
            if required_poi is None:
                continue
            key = required_poi.name.strip().lower()
            if key in owner:
                continue
            target_idx = min(range(len(normalized_plans)), key=lambda idx: len(normalized_plans[idx]["spots"]))
            normalized_plans[target_idx]["spots"].append(required_poi)
            owner[key] = target_idx

        final_plans = [plan for plan in normalized_plans if plan["spots"]]
        for plan in final_plans:
            plan["theme"] = self._build_theme(plan["spots"])
            notes = self._build_notes(plan["spots"], request.style)
            if missing_names:
                notes.append(f"以下必达景点未命中在线候选：{'、'.join(missing_names)}，已继续按现有点位规划。")
            plan["notes"] = notes
        return final_plans

    def _required_spot_names(self, constraints: TravelConstraints) -> list[str]:
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

    def _find_poi_for_required_name(self, required_name: str, candidates: list[PointOfInterest]) -> PointOfInterest | None:
        required_norm = self._normalize_text(required_name)
        if not required_norm:
            return None
        for poi in candidates:
            poi_norm = self._normalize_text(poi.name)
            if poi_norm == required_norm or required_norm in poi_norm or poi_norm in required_norm:
                return poi
        return None

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"[\s·・,，。；:：\-_/()（）]+", "", value.strip().lower())

    def _pick_closest_candidate(self, existing_spots: list[PointOfInterest], candidates: list[PointOfInterest]) -> PointOfInterest:
        if not existing_spots:
            return candidates[0]
        anchor = self._cluster_center(existing_spots)
        return min(
            candidates,
            key=lambda poi: (
                self._distance(anchor, poi),
                0 if poi.district in {spot.district for spot in existing_spots} else 1,
                poi.ticket_cost,
            ),
        )

    def _cluster_day_buckets(
        self,
        pois: list[PointOfInterest],
        days: int,
        target_per_day: int,
    ) -> list[list[PointOfInterest]]:
        anchor_count = min(days, len(pois))
        anchors = self._choose_day_anchors(pois, anchor_count)
        buckets: list[list[PointOfInterest]] = [[anchor] for anchor in anchors]
        remaining = [poi for poi in pois if poi not in anchors]
        capacities = [target_per_day for _ in range(anchor_count)]

        for poi in remaining:
            bucket_index = self._best_bucket_for_poi(poi, buckets, capacities)
            buckets[bucket_index].append(poi)

        ordered_buckets = [self._order_bucket(bucket) for bucket in buckets]
        while len(ordered_buckets) < days:
            ordered_buckets.append([])
        return ordered_buckets

    def _choose_day_anchors(self, pois: list[PointOfInterest], anchor_count: int) -> list[PointOfInterest]:
        if anchor_count <= 0:
            return []
        anchors = [pois[0]]
        remaining = pois[1:]
        while len(anchors) < anchor_count and remaining:
            next_anchor = max(
                remaining,
                key=lambda poi: (
                    min(self._distance(candidate, poi) for candidate in anchors),
                    -poi.ticket_cost,
                ),
            )
            anchors.append(next_anchor)
            remaining = [poi for poi in remaining if poi != next_anchor]
        return anchors

    def _best_bucket_for_poi(
        self,
        poi: PointOfInterest,
        buckets: list[list[PointOfInterest]],
        capacities: list[int],
    ) -> int:
        candidate_indexes = [index for index, bucket in enumerate(buckets) if len(bucket) < capacities[index]]
        if not candidate_indexes:
            candidate_indexes = list(range(len(buckets)))
        return min(
            candidate_indexes,
            key=lambda index: self._bucket_cost(poi, buckets[index], capacities[index]),
        )

    def _bucket_cost(
        self,
        poi: PointOfInterest,
        bucket: list[PointOfInterest],
        capacity: int,
    ) -> tuple[float, float, float]:
        center = self._cluster_center(bucket)
        distance = self._distance(center, poi)
        district_penalty = 0.0 if poi.district in {item.district for item in bucket} else 1.2
        long_trip_penalty = 0.0
        if distance > 25.0:
            long_trip_penalty = 14.0
        elif distance > 15.0:
            long_trip_penalty = 6.0
        elif distance > 8.0:
            long_trip_penalty = 2.5
        fullness_penalty = max(0, len(bucket) - capacity + 1) * 0.8
        return (distance + district_penalty + long_trip_penalty + fullness_penalty, distance, poi.ticket_cost)

    def _order_bucket(self, bucket: list[PointOfInterest]) -> list[PointOfInterest]:
        if len(bucket) <= 2:
            return list(bucket)
        center = self._cluster_center(bucket)
        start = min(bucket, key=lambda poi: self._distance(center, poi))
        remaining = [poi for poi in bucket if poi != start]
        ordered = [start]
        while remaining:
            current = ordered[-1]
            next_poi = min(
                remaining,
                key=lambda poi: (
                    self._distance(current, poi),
                    0 if poi.district == current.district else 1,
                    poi.ticket_cost,
                ),
            )
            ordered.append(next_poi)
            remaining = [poi for poi in remaining if poi != next_poi]
        return ordered

    def _prefer_dense_core(
        self,
        ranked_pois: list[PointOfInterest],
        days: int,
        max_daily_spots: int,
    ) -> list[PointOfInterest]:
        if not ranked_pois:
            return []
        target_count = min(len(ranked_pois), max(days * max(3, max_daily_spots), days + 2))
        seed = ranked_pois[0]
        scored = sorted(
            ranked_pois,
            key=lambda poi: (
                self._distance(seed, poi)
                + (12.0 if self._distance(seed, poi) > 20.0 else 4.0 if self._distance(seed, poi) > 10.0 else 0.0),
                poi.ticket_cost,
            ),
        )
        preferred = scored[:target_count]
        ranked_preferred = [poi for poi in ranked_pois if poi in preferred]
        return ranked_preferred or ranked_pois

    def _cluster_center(self, spots: list[PointOfInterest]) -> PointOfInterest:
        avg_lat = sum(spot.lat for spot in spots) / len(spots)
        avg_lon = sum(spot.lon for spot in spots) / len(spots)
        return min(spots, key=lambda spot: self._haversine(avg_lat, avg_lon, spot.lat, spot.lon))

    def _distance(self, left: PointOfInterest, right: PointOfInterest) -> float:
        return self._haversine(left.lat, left.lon, right.lat, right.lon)

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
        )
        return 2 * radius * math.asin(math.sqrt(a))
