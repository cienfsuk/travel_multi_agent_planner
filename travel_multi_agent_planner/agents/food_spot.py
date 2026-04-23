from __future__ import annotations

import math

from ..models import FoodVenue, MealRecommendation, TripRequest


class FoodSpotAgent:
    def attach_meals(
        self,
        request: TripRequest,
        daily_spot_plans: list[dict],
        food_options: list[FoodVenue] | None = None,
        used_food_keys: set[str] | None = None,
    ) -> list[dict]:
        self._active_budget_mode = request.food_budget_preference
        used_keys = used_food_keys if used_food_keys is not None else set()
        fallback_foods = list(food_options or [])
        meal_jobs = self._build_meal_jobs(daily_spot_plans, fallback_foods)
        job_cursor = 0
        for index, plan in enumerate(daily_spot_plans, start=1):
            day_index = int(plan.get("day", index))
            meal_paths = plan.get("meal_paths", {})
            lunch_candidates = meal_jobs[job_cursor]["candidates"] if job_cursor < len(meal_jobs) else fallback_foods
            lunch_anchor = self._meal_anchor(plan["spots"], "lunch")
            dinner_anchor = self._meal_anchor(plan["spots"], "dinner")
            day_used_keys: set[str] = set()
            lunch_allow_repeat, lunch_available, lunch_remaining = self._repeat_policy(meal_jobs, job_cursor, used_keys, day_used_keys)
            lunch, lunch_key = self._pick_food(
                "lunch",
                lunch_candidates,
                request,
                used_keys,
                day_used_keys,
                day_index,
                lunch_anchor,
                meal_paths.get("lunch", []),
                allow_repeat=lunch_allow_repeat,
            )
            used_keys.add(lunch_key)
            day_used_keys.add(lunch_key)
            job_cursor += 1
            dinner_candidates = meal_jobs[job_cursor]["candidates"] if job_cursor < len(meal_jobs) else fallback_foods
            dinner_allow_repeat, dinner_available, dinner_remaining = self._repeat_policy(meal_jobs, job_cursor, used_keys, day_used_keys)
            dinner, dinner_key = self._pick_food(
                "dinner",
                dinner_candidates,
                request,
                used_keys,
                day_used_keys,
                day_index,
                dinner_anchor,
                meal_paths.get("dinner", []),
                allow_repeat=dinner_allow_repeat,
            )
            used_keys.add(dinner_key)
            day_used_keys.add(dinner_key)
            job_cursor += 1
            notes = plan.setdefault("notes", [])
            notes.append(self._pool_note("午餐", lunch_candidates))
            notes.append(self._pool_note("晚餐", dinner_candidates))
            if lunch_allow_repeat:
                notes.append(f"午餐候选覆盖度不足：剩余 {lunch_remaining} 餐仅有 {lunch_available} 个未复用候选。")
            if dinner_allow_repeat:
                notes.append(f"晚餐候选覆盖度不足：剩余 {dinner_remaining} 餐仅有 {dinner_available} 个未复用候选。")
            if lunch.fallback_used:
                notes.append(f"午餐候选不足，已允许回退使用重复餐饮：{lunch.venue_name}。")
            if dinner.fallback_used:
                notes.append(f"晚餐候选不足，已允许回退使用重复餐饮：{dinner.venue_name}。")
            notes.append(f"午餐使用 {self._selection_tier_text(lunch.selection_tier)}候选，主来源：{self._primary_source(lunch_candidates)}。")
            notes.append(f"晚餐使用 {self._selection_tier_text(dinner.selection_tier)}候选，主来源：{self._primary_source(dinner_candidates)}。")
            if lunch.route_distance_km > 1.0:
                notes.append(f"午餐 {lunch.venue_name} 距离午餐主路线约 {lunch.route_distance_km:.1f} km，因近距离候选不足已放宽搜索半径。")
            if dinner.route_distance_km > 1.5:
                notes.append(f"晚餐 {dinner.venue_name} 距离晚餐主路线约 {dinner.route_distance_km:.1f} km，因近距离候选不足已放宽搜索半径。")
            plan["meals"] = [lunch, dinner]
        return daily_spot_plans

    def _pick_food(
        self,
        meal_type: str,
        candidates: list[FoodVenue],
        request: TripRequest,
        used_keys: set[str],
        day_used_keys: set[str],
        day_index: int,
        anchor: tuple[float, float] | None = None,
        route_path: list[list[float]] | None = None,
        allow_repeat: bool = False,
    ) -> tuple[MealRecommendation, str]:
        candidates_with_distance = [
            (food, self._route_distance(food, route_path), self._anchor_distance(food, anchor))
            for food in candidates
        ]
        if not candidates_with_distance:
            fallback_food = self._build_fallback_food(meal_type, day_index)
            estimated_cost = self._cost_for_meal(fallback_food, meal_type, day_index)
            meal = MealRecommendation(
                venue_name=fallback_food.name,
                meal_type=meal_type,  # type: ignore[arg-type]
                estimated_cost=estimated_cost,
                reason=f"{meal_type} 候选不足，已按降级策略补齐餐饮占位。",
                venue_district=fallback_food.district,
                cuisine=fallback_food.cuisine,
                lat=fallback_food.lat,
                lon=fallback_food.lon,
                anchor_distance_km=0.0,
                route_distance_km=0.0,
                fallback_used=True,
                selection_tier="fallback",
                source_evidence=fallback_food.source_evidence[:1],
            )
            return meal, self._food_key(fallback_food)
        distance_ladders = self._distance_ladders(meal_type)
        selection_pool: list[tuple[FoodVenue, float, float]] = []
        selection_tier = "strict"
        for tier_index, max_distance in enumerate(distance_ladders):
            nearby_unused = [
                (food, route_distance, anchor_distance)
                for food, route_distance, anchor_distance in candidates_with_distance
                if self._food_key(food) not in used_keys and self._food_key(food) not in day_used_keys and route_distance <= max_distance
            ]
            if nearby_unused:
                selection_pool = nearby_unused
                selection_tier = self._selection_tier_label(tier_index)
                break
        fallback_used = False
        if not selection_pool and allow_repeat:
            nearby_all: list[tuple[FoodVenue, float, float]] = []
            for tier_index, max_distance in enumerate(distance_ladders):
                nearby_all = [
                    (food, route_distance, anchor_distance)
                    for food, route_distance, anchor_distance in candidates_with_distance
                    if self._food_key(food) not in day_used_keys and route_distance <= max_distance
                ]
                if nearby_all:
                    selection_pool = nearby_all
                    fallback_used = True
                    selection_tier = self._selection_tier_label(tier_index)
                    break
        if not selection_pool:
            unique_candidates = [
                (food, route_distance, anchor_distance)
                for food, route_distance, anchor_distance in candidates_with_distance
                if self._food_key(food) not in used_keys and self._food_key(food) not in day_used_keys
            ]
            if unique_candidates:
                selection_pool = unique_candidates
                selection_tier = "fallback"
            else:
                selection_pool = [
                    (food, route_distance, anchor_distance)
                    for food, route_distance, anchor_distance in candidates_with_distance
                    if self._food_key(food) not in day_used_keys
                ] or candidates_with_distance
                fallback_used = True
                selection_tier = "fallback"
        ranked = sorted(
            selection_pool,
            key=lambda item: self._food_score(item[0], request, meal_type, used_keys, anchor, item[1], item[2]),
            reverse=True,
        )
        chosen, route_distance, anchor_distance = ranked[0]
        estimated_cost = self._cost_for_meal(chosen, meal_type, day_index)
        reason = self._build_reason(chosen, meal_type, request.interests, request.food_tastes)
        if route_distance > self._distance_threshold(meal_type):
            reason = f"{reason} 当前店铺距离当餐主路线约 {route_distance:.1f} km。"
        meal = MealRecommendation(
            venue_name=chosen.name,
            meal_type=meal_type,  # type: ignore[arg-type]
            estimated_cost=estimated_cost,
            reason=reason,
            venue_district=chosen.district,
            cuisine=chosen.cuisine,
            lat=chosen.lat,
            lon=chosen.lon,
            anchor_distance_km=round(anchor_distance, 2),
            route_distance_km=round(route_distance, 2),
            fallback_used=fallback_used,
            selection_tier=selection_tier,
            source_evidence=chosen.source_evidence[:2],
        )
        return meal, self._food_key(chosen)

    def _build_meal_jobs(self, daily_spot_plans: list[dict], fallback_foods: list[FoodVenue]) -> list[dict]:
        jobs: list[dict] = []
        for plan in daily_spot_plans:
            pools = plan.get("meal_candidate_pools", {})
            jobs.append({"day": plan.get("day"), "meal_type": "lunch", "candidates": self._candidate_pool(plan, "lunch", fallback_foods, pools)})
            jobs.append({"day": plan.get("day"), "meal_type": "dinner", "candidates": self._candidate_pool(plan, "dinner", fallback_foods, pools)})
        return jobs

    def _candidate_pool(self, plan: dict, meal_type: str, fallback_foods: list[FoodVenue], pools: dict) -> list[FoodVenue]:
        pooled = list(pools.get(meal_type) or [])
        if pooled:
            return pooled
        districts = {spot.district for spot in plan["spots"]}
        fallback_matches = [
            food
            for food in fallback_foods
            if food.district in districts or "沿途候选" in food.tags or food.meal_suitability in {meal_type, "both"}
        ]
        return fallback_matches or list(fallback_foods)

    def _repeat_policy(self, meal_jobs: list[dict], start_index: int, used_keys: set[str], day_used_keys: set[str]) -> tuple[bool, int, int]:
        pending_jobs = meal_jobs[start_index:]
        remaining_slots = len(pending_jobs)
        available_keys: set[str] = set()
        for job in pending_jobs:
            for food in job.get("candidates", []):
                key = self._food_key(food)
                if key not in used_keys and key not in day_used_keys:
                    available_keys.add(key)
        return len(available_keys) < remaining_slots, len(available_keys), remaining_slots

    def _pool_note(self, meal_label: str, candidates: list[FoodVenue]) -> str:
        if not candidates:
            return f"{meal_label}候选池为空，需退回全局餐饮候选。"
        distinct_count = len({self._food_key(food) for food in candidates})
        return f"{meal_label}候选池去重后 {distinct_count} 家，来源：{self._source_breakdown(candidates)}。"

    def _source_breakdown(self, candidates: list[FoodVenue]) -> str:
        counters: dict[str, int] = {}
        for food in candidates:
            source = self._food_source(food)
            counters[source] = counters.get(source, 0) + 1
        ordered = sorted(counters.items(), key=lambda item: (-item[1], item[0]))
        return "、".join(f"{label} {count}" for label, count in ordered[:4]) or "基础候选"

    def _primary_source(self, candidates: list[FoodVenue]) -> str:
        if not candidates:
            return "基础候选"
        counters: dict[str, int] = {}
        for food in candidates:
            source = self._food_source(food)
            counters[source] = counters.get(source, 0) + 1
        return sorted(counters.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def _food_source(self, food: FoodVenue) -> str:
        tags = set(food.tags)
        for label in ["沿途候选", "近邻候选", "区域候选", "全局候选", "地方风味"]:
            if label in tags:
                return label
        return "基础候选"

    def _selection_tier_text(self, tier: str) -> str:
        mapping = {"strict": "严格", "nearby": "近邻", "expanded": "扩展", "max-radius": "最大半径", "fallback": "回退"}
        return mapping.get(tier, tier)

    def _food_score(
        self,
        food: FoodVenue,
        request: TripRequest,
        meal_type: str,
        used_keys: set[str],
        anchor: tuple[float, float] | None = None,
        route_distance: float | None = None,
        anchor_distance: float | None = None,
    ) -> tuple[float, float, float, float, float]:
        interest_hits = len(set(request.interests).intersection(food.tags))
        taste_hits = len(set(request.food_tastes).intersection(set(food.taste_profile) | set(food.tags)))
        budget_fit_map = {
            "budget": 2.0 if food.average_cost <= 60 else 0.0,
            "balanced": 2.0 if 40 <= food.average_cost <= 120 else 0.5,
            "premium": 2.0 if food.average_cost >= 80 else 0.8,
        }
        suitability = 1.5 if food.meal_suitability in {meal_type, "both"} else -1.0
        repeat_penalty = -9.0 if self._food_key(food) in used_keys else 0.0
        route_bonus = 1.1 if "沿途候选" in food.tags else 0.0
        route_distance = route_distance if route_distance is not None else self._route_distance(food, None)
        anchor_distance = anchor_distance if anchor_distance is not None else self._anchor_distance(food, anchor)
        route_proximity_bonus = self._distance_bonus(route_distance)
        anchor_proximity_bonus = self._distance_bonus(anchor_distance) * 0.5
        return (
            interest_hits + taste_hits + suitability + budget_fit_map[request.food_budget_preference] + repeat_penalty + route_bonus + route_proximity_bonus + anchor_proximity_bonus,
            -route_distance,
            -anchor_distance,
            food.average_cost if meal_type == "dinner" else -food.average_cost,
            -len(food.source_evidence),
        )

    def _cost_for_meal(self, food: FoodVenue, meal_type: str, day_index: int) -> float:
        base = food.average_cost
        meal_factor = 0.88 if meal_type == "lunch" else 1.22
        district_factor = self._district_factor(food)
        venue_factor = self._venue_factor(food)
        budget_factor = self._budget_factor(meal_type)
        day_adjust = self._day_variation(day_index)
        deterministic_bias = self._name_bias(food.name)
        estimated = base * meal_factor * district_factor * venue_factor * budget_factor + day_adjust + deterministic_bias
        floor = 22.0 if meal_type == "lunch" else 48.0
        return round(max(floor, estimated), 2)

    def _build_reason(
        self,
        food: FoodVenue,
        meal_type: str,
        interests: list[str],
        tastes: list[str],
    ) -> str:
        interest_hint = "、".join(interests[:2]) if interests else "城市体验"
        taste_hint = "、".join(tastes[:2]) if tastes else "本地风味"
        meal_label = "午餐" if meal_type == "lunch" else "晚餐"
        return f"{meal_label}安排在 {food.name}，兼顾 {interest_hint} 与 {food.cuisine}，口味更贴近 {taste_hint}。"

    def _district_factor(self, food: FoodVenue) -> float:
        text = f"{food.district} {food.description} {' '.join(food.tags)}"
        premium_hits = ["景区", "商圈", "核心", "步行街", "老门东", "夫子庙", "新街口", "西湖", "外滩"]
        relaxed_hits = ["社区", "居民", "大学", "生活区", "巷子", "街边"]
        if any(token in text for token in premium_hits):
            return 1.16
        if any(token in text for token in relaxed_hits):
            return 0.92
        return 1.0

    def _venue_factor(self, food: FoodVenue) -> float:
        text = f"{food.name} {food.cuisine} {food.description}"
        if any(token in text for token in ["小吃", "面", "粉", "快餐", "盖饭", "茶铺"]):
            return 0.82
        if any(token in text for token in ["本帮", "私房", "烤肉", "火锅", "海鲜", "夜宵", "酒楼"]):
            return 1.18
        if any(token in text for token in ["咖啡", "甜品", "轻食", "茶餐厅"]):
            return 0.96
        return 1.0

    def _budget_factor(self, meal_type: str) -> float:
        budget_mode = getattr(self, "_active_budget_mode", "balanced")
        table = {
            "budget": {"lunch": 0.88, "dinner": 0.94},
            "balanced": {"lunch": 1.0, "dinner": 1.06},
            "premium": {"lunch": 1.16, "dinner": 1.28},
        }
        return table.get(budget_mode, table["balanced"])[meal_type]

    def _day_variation(self, day_index: int) -> float:
        pattern = {1: 0.0, 2: 6.0, 3: -3.0, 4: 8.0, 5: 2.0, 6: 10.0, 7: 4.0}
        return pattern.get(day_index, 4.0)

    def _name_bias(self, name: str) -> float:
        return float(sum(ord(char) for char in name) % 11 - 5)

    def _food_key(self, food: FoodVenue) -> str:
        address = (food.address or "").strip().lower()
        district = food.district.strip().lower()
        if address:
            return f"{food.name.strip().lower()}|{address}"
        return f"{food.name.strip().lower()}|{district}"

    def _meal_anchor(self, spots: list, meal_type: str) -> tuple[float, float] | None:
        if not spots:
            return None
        if meal_type == "lunch":
            index = max(0, len(spots) // 2 - 1)
            return spots[index].lat, spots[index].lon
        return spots[-1].lat, spots[-1].lon

    def _distance_bonus(self, distance: float) -> float:
        if distance <= 0.8:
            return 2.4
        if distance <= 1.5:
            return 1.5
        if distance <= 2.5:
            return 0.8
        if distance <= 4.0:
            return -0.5
        return -1.5

    def _anchor_distance(self, food: FoodVenue, anchor: tuple[float, float] | None) -> float:
        if anchor is None or not food.lat or not food.lon:
            return 999.0
        return self._haversine(anchor[0], anchor[1], food.lat, food.lon)

    def _route_distance(self, food: FoodVenue, route_path: list[list[float]] | None) -> float:
        if not route_path or len(route_path) < 2 or not food.lat or not food.lon:
            return 999.0
        min_distance = min(
            self._point_to_segment_distance(food.lat, food.lon, start[1], start[0], end[1], end[0])
            for start, end in zip(route_path, route_path[1:])
        )
        return min_distance

    def _is_near_route(self, food: FoodVenue, meal_paths: dict) -> bool:
        return any(self._route_distance(food, path) <= 2.5 for path in meal_paths.values() if path)

    def _distance_ladders(self, meal_type: str) -> list[float]:
        return [1.0, 1.2, 1.8, 2.5] if meal_type == "lunch" else [1.5, 1.8, 2.2, 2.5]

    def _distance_threshold(self, meal_type: str) -> float:
        return 1.0 if meal_type == "lunch" else 1.5

    def _selection_tier_label(self, tier_index: int) -> str:
        return ["strict", "nearby", "expanded", "max-radius"][min(tier_index, 3)]

    def _point_to_segment_distance(
        self,
        point_lat: float,
        point_lon: float,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
    ) -> float:
        origin_lat = math.radians((start_lat + end_lat + point_lat) / 3)
        px, py = self._project_xy(point_lat, point_lon, origin_lat)
        sx, sy = self._project_xy(start_lat, start_lon, origin_lat)
        ex, ey = self._project_xy(end_lat, end_lon, origin_lat)
        dx = ex - sx
        dy = ey - sy
        if dx == 0 and dy == 0:
            return math.hypot(px - sx, py - sy)
        t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
        nearest_x = sx + dx * t
        nearest_y = sy + dy * t
        return math.hypot(px - nearest_x, py - nearest_y)

    def _project_xy(self, lat: float, lon: float, origin_lat: float) -> tuple[float, float]:
        radius = 6371.0
        x = math.radians(lon) * radius * math.cos(origin_lat)
        y = math.radians(lat) * radius
        return x, y

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
        )
        return 2 * radius * math.asin(math.sqrt(a))

    def _build_fallback_food(self, meal_type: str, day_index: int) -> FoodVenue:
        meal_label = "午餐" if meal_type == "lunch" else "晚餐"
        return FoodVenue(
            name=f"当日{meal_label}推荐{day_index}",
            district="市中心",
            cuisine="本地风味",
            description=f"{meal_label}在线候选不足后的降级补齐项",
            average_cost=55.0 if meal_type == "lunch" else 88.0,
            tags=["food", "fallback"],
            taste_profile=[],
            meal_suitability=meal_type if meal_type in {"lunch", "dinner"} else "both",  # type: ignore[arg-type]
            lat=0.0,
            lon=0.0,
            source_evidence=[],
        )
