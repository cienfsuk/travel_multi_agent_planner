from __future__ import annotations

import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict

from .agents import (
    BudgetAgent,
    ConstraintValidatorAgent,
    FoodSpotAgent,
    HotelAgent,
    PlannerAgent,
    RequirementAgent,
    SearchAgent,
    TransportAgent,
    TravelNotesAgent,
    WebGuideAgent,
)
from .config import AppConfig
from .models import AgentTraceStep, DayPlan, ProviderStatus, RoutePoint, TripPlan, TripRequest
from .providers import BailianLLMProvider, ChinaRailway12306Provider, TencentMapProvider, TencentMapSearchProvider
from .scheduling import segment_arrival_minutes, segment_departure_minutes, spot_time_bucket, build_transport_nodes


class TravelPlanningOrchestrator:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig.from_env()
        self.requirement = RequirementAgent()
        self.search_agent = SearchAgent()
        self.planner = PlannerAgent()
        self.food_spot = FoodSpotAgent()
        self.hotel_agent = HotelAgent()
        self.travel_notes_agent = TravelNotesAgent()
        self.budget = BudgetAgent()
        self.validator = ConstraintValidatorAgent()
        self.web_guide = WebGuideAgent()

        self.llm_provider = BailianLLMProvider(self.config.dashscope_api_key, self.config.bailian_model)
        self.search_provider = TencentMapSearchProvider(self.config.tencent_map_server_key)
        self.map_provider = TencentMapProvider(self.config.tencent_map_server_key)
        self.intercity_provider = ChinaRailway12306Provider()
        self.transport = TransportAgent(intercity_provider=self.intercity_provider, llm_provider=self.llm_provider)

    def current_mode(self) -> str:
        return self.config.resolve_mode()

    def provider_statuses(self) -> list[ProviderStatus]:
        return [
            ProviderStatus(
                name="百炼大模型",
                active=self.llm_provider.is_available(),
                detail=f"模型={self.config.bailian_model}" if self.llm_provider.is_available() else "未配置 DASHSCOPE_API_KEY，将使用本地规则替代部分整理能力",
            ),
            ProviderStatus(
                name="腾讯位置服务（服务端）",
                active=self.search_provider.is_available(),
                detail="用于城市确认、POI/酒店/餐饮检索与路线规划" if self.search_provider.is_available() else "缺少 TENCENT_MAP_SERVER_KEY",
            ),
            ProviderStatus(
                name="腾讯地图（前端 JS）",
                active=self.config.has_tencent_js(),
                detail="用于浏览器端本地动画播放器" if self.config.has_tencent_js() else "缺少 TENCENT_MAP_JS_KEY，无法启用主播放器",
            ),
            ProviderStatus(
                name="中国铁路12306",
                active=self.intercity_provider.is_available(),
                detail="用于查询城际铁路车次、发到时间和票价",
            ),
        ]

    def create_plan(
        self,
        request: TripRequest,
        on_trace: "Callable[[AgentTraceStep], None] | None" = None,
    ) -> TripPlan:
        trace: list[AgentTraceStep] = []
        warnings: list[str] = []
        search_notes: list[str] = []
        mode = self.current_mode()
        provider_statuses = self.provider_statuses()

        def _emit(step: AgentTraceStep) -> None:
            trace.append(step)
            if on_trace is not None:
                on_trace(step)

        if mode == "fallback":
            raise RuntimeError("当前版本已移除系统合成与降级展示，请使用在线模式并配置腾讯位置服务。")
        if not self.search_provider.is_available():
            raise RuntimeError("缺少 TENCENT_MAP_SERVER_KEY，无法生成可靠在线方案。")
        if not self.config.has_tencent_js():
            raise RuntimeError("缺少 TENCENT_MAP_JS_KEY，无法生成浏览器端本地动画播放器。")
        constraints, constraint_detail = self.requirement.build_constraints(
            request,
            self.llm_provider if self.llm_provider.is_available() else None,
        )
        _emit(
            AgentTraceStep(
                agent_name="Requirement Agent",
                input_summary=f"{request.origin} -> {request.destination}，{request.days} 天，预算 {request.budget:.0f}",
                output_summary=f"约束：每日最多 {constraints.max_daily_spots} 个景点，最多 {constraints.max_daily_transport_transfers} 次跨区",
                key_decisions=[constraint_detail, constraints.pacing_note or "保持深度游节奏"],
                status="ok" if "百炼" in constraint_detail else "fallback",
            )
        )

        profile, origin_match, destination_match, provider_notes = self.search_agent.build_city_profile(request, self.search_provider)
        profile.pois, required_notes = self.search_agent.ensure_required_spots(profile, constraints, self.search_provider)
        profile.pois = self.search_agent.rank_pois(profile.pois, request, constraints)
        search_notes.extend(required_notes)
        search_notes.extend(provider_notes)
        _emit(
            AgentTraceStep(
                agent_name="Search Agent",
                input_summary=f"确认城市：{request.origin} / {request.destination}",
                output_summary=f"已确认 {origin_match.confirmed_name} -> {destination_match.confirmed_name}，并检索景点 {len(profile.pois)} 个、餐饮 {len(profile.foods)} 个、酒店 {len(profile.hotels)} 个",
                key_decisions=(required_notes + provider_notes)[:3] or [f"目标城市标准化为 {destination_match.confirmed_name}"],
            )
        )

        travel_notes, note_fetch_notes = self.travel_notes_agent.collect(
            request,
            profile,
            self.search_provider,
            self.llm_provider if self.llm_provider.is_available() else None,
        )
        search_notes.extend(note_fetch_notes)
        _emit(
            AgentTraceStep(
                agent_name="Travel Notes Agent",
                input_summary=f"基于 {profile.city} 的真实点位生成攻略摘要",
                output_summary=f"获得 {len(travel_notes)} 条攻略摘要",
                key_decisions=note_fetch_notes[:2] or ["攻略摘要为空，不影响主体行程"],
            )
        )

        draft_plan, planner_detail = self.planner.create_daily_spot_plan(
            request,
            profile.pois,
            constraints,
            self.llm_provider if self.llm_provider.is_available() else None,
        )
        _emit(
            AgentTraceStep(
                agent_name="Route Planner Agent",
                input_summary=f"候选景点 {len(profile.pois)} 个，攻略摘要 {len(travel_notes)} 条",
                output_summary=f"生成 {len(draft_plan)} 天初版路线",
                key_decisions=[planner_detail, f"核心兴趣：{', '.join(request.interests)}"],
                status="ok" if "百炼" in planner_detail else "fallback",
            )
        )

        day_plans, route_points, materialize_notes = self._materialize_day_plans(
            request,
            profile,
            destination_match.city_code,
            draft_plan,
        )
        search_notes.extend(materialize_notes)
        _emit(
            AgentTraceStep(
                agent_name="Hotel Agent",
                input_summary=f"酒店候选 {len(profile.hotels)} 个",
                output_summary=f"为 {len(day_plans)} 天行程匹配酒店",
                key_decisions=materialize_notes[:2] or ["根据预算、区域和点位距离完成酒店选择"],
            )
        )
        _emit(
            AgentTraceStep(
                agent_name="Transport Agent",
                input_summary=f"为 {len(day_plans)} 天生成城际与市内路线",
                output_summary="完成高铁/飞机与城市内交通分段",
                key_decisions=[day.transport.route_summary for day in day_plans[:2]] or [profile.local_transport_tip],
            )
        )

        budget_summary = self._build_budget(request, profile, day_plans)
        _emit(
            AgentTraceStep(
                agent_name="Budget Agent",
                input_summary=f"预算上限 {request.budget:.0f} 元",
                output_summary=f"总估算 {budget_summary.total_estimated:.0f} 元",
                key_decisions=[line.note for line in budget_summary.lines[:3]],
                status="ok" if budget_summary.is_within_budget else "warning",
            )
        )

        validation_issues, score = self.validator.validate(request, constraints, day_plans, budget_summary, destination_match)
        _emit(
            AgentTraceStep(
                agent_name="Constraint Validator Agent",
                input_summary=f"校验 {len(day_plans)} 天路线、证据、酒店、餐饮和交通",
                output_summary=f"发现 {len(validation_issues)} 个问题，评分 {score:.0f}",
                key_decisions=[issue.message for issue in validation_issues[:3]] or ["未发现明显冲突"],
                status="ok" if not validation_issues else "warning",
            )
        )

        was_revised = False
        if self.validator.should_revise(validation_issues):
            revised_plan, revision_detail = self.planner.revise_daily_spot_plan(
                request,
                profile.pois,
                constraints,
                draft_plan,
                validation_issues,
                self.llm_provider if self.llm_provider.is_available() else None,
            )
            revised_day_plans, route_points, revised_notes = self._materialize_day_plans(
                request,
                profile,
                destination_match.city_code,
                revised_plan,
            )
            revised_budget = self._build_budget(request, profile, revised_day_plans)
            revised_issues, revised_score = self.validator.validate(request, constraints, revised_day_plans, revised_budget, destination_match)
            if revised_score >= score or len(revised_issues) <= len(validation_issues):
                day_plans = revised_day_plans
                budget_summary = revised_budget
                validation_issues = revised_issues
                score = revised_score
                was_revised = True
                search_notes.extend(revised_notes)
            _emit(
                AgentTraceStep(
                    agent_name="Route Planner Agent",
                    input_summary=f"收到 {len(validation_issues)} 条校验问题",
                    output_summary=f"完成 1 次路线修正，当前评分 {score:.0f}",
                    key_decisions=[revision_detail, "仅执行一次自动修正以保证流程稳定"],
                    status="ok" if was_revised else "fallback",
                )
            )

        evidence_mode_summary = self._build_evidence_summary(day_plans, travel_notes)
        summary_markdown = self._build_guide(
            request,
            constraints,
            profile,
            origin_match,
            destination_match,
            day_plans,
            budget_summary,
            validation_issues,
            mode,
            provider_statuses,
            travel_notes,
            evidence_mode_summary,
        )
        _emit(
            AgentTraceStep(
                agent_name="Web Guide Agent",
                input_summary="整合路线、酒店、预算、真实来源证据与攻略摘要",
                output_summary="生成中文页面展示数据与旅行手册",
                key_decisions=["导出 JSON/Markdown", f"当前运行模式为 {mode}", evidence_mode_summary],
            )
        )

        warnings.extend(issue.message for issue in validation_issues if issue.severity in {"high", "medium"})
        return TripPlan(
            request=request,
            constraints=constraints,
            city_profile=profile,
            origin_match=origin_match,
            destination_match=destination_match,
            day_plans=day_plans,
            budget_summary=budget_summary,
            validation_issues=validation_issues,
            summary_markdown=summary_markdown,
            route_points=route_points,
            warnings=warnings,
            trace=trace,
            final_score=score,
            mode=mode,  # type: ignore[arg-type]
            provider_statuses=provider_statuses,
            travel_notes=travel_notes,
            evidence_mode_summary=evidence_mode_summary,
            was_revised=was_revised,
            search_notes=search_notes,
        )

    def _materialize_day_plans(
        self,
        request: TripRequest,
        profile,
        city_code: str,
        draft_plan: list[dict],
    ) -> tuple[list[DayPlan], list[RoutePoint], list[str]]:
        notes: list[str] = []
        hotel_candidates = self._prepare_hotel_candidates(request, profile)
        hotel_input, hotel_notes = self.hotel_agent.attach_hotels(
            request,
            deepcopy(draft_plan),
            hotel_candidates,
            self.llm_provider if self.llm_provider.is_available() else None,
        )
        notes.extend(hotel_notes)
        weather = self.search_provider.weather(city_code)
        used_food_keys: set[str] = set()

        day_plans: list[DayPlan] = []
        route_points: list[RoutePoint] = []
        for daily in hotel_input:
            hotel = daily.get("hotel")
            if hotel is None:
                if hotel_candidates:
                    hotel = hotel_candidates[0]
                    notes.append(f"第 {daily['day']} 天酒店偏好未命中，已降级使用 {hotel.name} 继续规划。")
                else:
                    notes.append(f"第 {daily['day']} 天未检索到可用酒店，已按无酒店模式继续规划。")
            if daily["spots"]:
                if hotel is not None:
                    anchor_lat, anchor_lon = hotel.lat, hotel.lon
                else:
                    anchor_lat, anchor_lon = daily["spots"][0].lat, daily["spots"][0].lon
                daily["spots"] = self.map_provider.reorder_spots({"lat": anchor_lat, "lon": anchor_lon}, daily["spots"])
            arrival_segment = None
            departure_segment = None
            stay_anchor = hotel.name if hotel is not None else (daily["spots"][0].name if daily["spots"] else f"{profile.city} 市区")
            if daily["day"] == 1:
                arrival_segment = self.transport.infer_intercity_segment(
                    request,
                    profile,
                    f"{request.origin} 出发",
                    stay_anchor,
                    leg="outbound",
                )
            if daily["day"] == request.days:
                departure_segment = self.transport.infer_intercity_segment(
                    request,
                    profile,
                    stay_anchor,
                    f"{request.origin} 返程",
                    leg="return",
                )
            daily["spots"] = self._arrange_spots_for_schedule(daily["spots"], arrival_segment, departure_segment)
            daily["meal_paths"] = self._build_meal_paths(hotel, daily["spots"])
            daily["meal_candidate_pools"] = {
                "lunch": self._build_meal_candidate_pool(profile.city, hotel, daily["spots"], request.food_tastes, "lunch", profile.foods, daily["meal_paths"].get("lunch", [])),
                "dinner": self._build_meal_candidate_pool(profile.city, hotel, daily["spots"], request.food_tastes, "dinner", profile.foods, daily["meal_paths"].get("dinner", [])),
            }
            notes.append(
                f"第 {daily['day']} 天午餐候选 {len({self._food_key(food) for food in daily['meal_candidate_pools']['lunch']})} 家，晚餐候选 {len({self._food_key(food) for food in daily['meal_candidate_pools']['dinner']})} 家。"
            )
            daily_with_meals = self.food_spot.attach_meals(
                request,
                [deepcopy(daily)],
                self._clone_foods_with_tag(profile.foods, "全局候选"),
                used_food_keys=used_food_keys,
            )[0]
            nodes = self._build_day_nodes(daily_with_meals, hotel, arrival_segment, departure_segment)
            segments = self.map_provider.route_segments(nodes)

            transport_plan = self.transport.build_day_transport(
                request,
                profile,
                daily_with_meals["spots"],
                hotel,
                segments,
                daily_with_meals["day"],
                request.days,
            )
            day_plan = DayPlan(
                day=daily_with_meals["day"],
                theme=daily_with_meals["theme"],
                spots=daily_with_meals["spots"],
                meals=daily_with_meals["meals"],
                hotel=hotel,
                transport=transport_plan,
                transport_segments=segments,
                notes=self._transport_execution_notes(arrival_segment, departure_segment)
                + daily_with_meals["notes"]
                + self._travel_execution_notes(hotel, daily_with_meals["meals"], weather.get("summary", "")),
                arrival_segment=arrival_segment,
                departure_segment=departure_segment,
                weather_summary=weather.get("summary", ""),
            )
            day_plans.append(day_plan)
            route_points.extend(self._build_route_points(day_plan))
        return day_plans, route_points, notes

    def _build_preliminary_day_nodes(self, daily: dict, hotel) -> list[dict]:
        nodes: list[dict] = [{"label": hotel.name, "lat": hotel.lat, "lon": hotel.lon, "kind": "hotel"}]
        for spot in daily["spots"]:
            nodes.append({"label": spot.name, "lat": spot.lat, "lon": spot.lon, "kind": "spot"})
        return nodes

    def _build_day_nodes(self, daily: dict, hotel, arrival_segment=None, departure_segment=None) -> list[dict]:
        return build_transport_nodes(
            hotel,
            daily["spots"],
            daily["meals"],
            arrival_segment=arrival_segment,
            departure_segment=departure_segment,
        )

    def _nodes_to_polyline(self, nodes: list[dict]) -> list[list[float]]:
        return [[node["lon"], node["lat"]] for node in nodes if node.get("lat") and node.get("lon")]

    def _build_meal_paths(self, hotel, spots: list) -> dict[str, list[list[float]]]:
        nodes = [{"lon": hotel.lon, "lat": hotel.lat}] if hotel else []
        nodes.extend({"lon": spot.lon, "lat": spot.lat} for spot in spots)
        if len(nodes) < 2:
            return {"lunch": [], "dinner": []}
        split_index = max(1, len(spots) // 2) if spots else 0
        lunch_nodes = nodes[: split_index + 2]
        dinner_nodes = nodes[max(0, len(nodes) - 3) :]
        return {
            "lunch": [[node["lon"], node["lat"]] for node in lunch_nodes],
            "dinner": [[node["lon"], node["lat"]] for node in dinner_nodes],
        }

    def _build_meal_candidate_pool(self, city_name: str, hotel, spots: list, tastes: list[str], meal_type: str, base_foods: list, meal_path: list[list[float]]) -> list:
        meal_label = "午餐" if meal_type == "lunch" else "晚餐"
        anchor = self._meal_anchor(hotel, spots, meal_type)
        area_hints = self._meal_area_hints(hotel, spots, meal_type)
        radii = [600, 1000, 1500, 2200] if meal_type == "lunch" else [800, 1200, 1800, 2500]
        merged = self._clone_foods_with_tag(base_foods, "全局候选")
        for radius_meters in radii:
            merged = self._merge_food_candidates(
                merged,
                self.search_provider.search_along_route_foods(city_name, meal_path, tastes, radius_meters=radius_meters) if meal_path else [],
            )
            if anchor is not None:
                merged = self._merge_food_candidates(
                    merged,
                    self.search_provider.search_nearby_foods(
                        city_name,
                        anchor[0],
                        anchor[1],
                        tastes,
                        radius_meters=radius_meters,
                        meal_type=meal_type,
                    ),
                )
            if len({self._food_key(food) for food in merged}) >= 7:
                break
        for area_hint in area_hints[:3]:
            if len({self._food_key(food) for food in merged}) >= 9:
                break
            merged = self._merge_food_candidates(merged, self.search_provider.search_area_foods(city_name, area_hint, tastes, meal_type))
            if len({self._food_key(food) for food in merged}) >= 9:
                break
        if meal_path:
            merged = self._dedupe_named_foods_by_route(merged, meal_path)
        if merged:
            merged[0].tags.append(f"{meal_label}候选")
        return merged

    def _transport_execution_notes(
        self,
        arrival_segment,
        departure_segment,
    ) -> list[str]:
        notes: list[str] = []
        if arrival_segment is not None:
            notes.append(
                f"入城交通：{arrival_segment.description} 预计 {arrival_segment.duration_minutes} 分钟，参考 {arrival_segment.estimated_cost:.0f} 元。"
            )
        if departure_segment is not None:
            notes.append(
                f"返程交通：{departure_segment.description} 预计 {departure_segment.duration_minutes} 分钟，参考 {departure_segment.estimated_cost:.0f} 元。"
            )
        return notes

    def _build_route_points(self, day_plan: DayPlan) -> list[RoutePoint]:
        route_points: list[RoutePoint] = [
            RoutePoint(
                day=day_plan.day,
                label=day_plan.hotel.name,
                lat=day_plan.hotel.lat,
                lon=day_plan.hotel.lon,
                kind="hotel",
                source=day_plan.hotel.source_evidence[0].provider if day_plan.hotel and day_plan.hotel.source_evidence else "tencent-map",
                slot_label="酒店",
            )
        ] if day_plan.hotel else []
        for index, spot in enumerate(day_plan.spots, start=1):
            route_points.append(
                RoutePoint(
                    day=day_plan.day,
                    label=spot.name,
                    lat=spot.lat,
                    lon=spot.lon,
                    kind="spot",
                    source=spot.source_evidence[0].provider if spot.source_evidence else "tencent-map",
                    slot_label=f"第 {index} 站",
                )
            )
        for meal in day_plan.meals:
            route_points.append(
                RoutePoint(
                    day=day_plan.day,
                    label=meal.venue_name,
                    lat=meal.lat,
                    lon=meal.lon,
                    kind=meal.meal_type,
                    source=meal.source_evidence[0].provider if meal.source_evidence else "tencent-map",
                    slot_label="午餐" if meal.meal_type == "lunch" else "晚餐",
                )
            )
        return route_points

    def _travel_execution_notes(self, hotel, meals: list, weather_summary: str) -> list[str]:
        notes = [f"酒店安排在 {hotel.district}，便于串联当天主路线。"] if hotel else []
        if weather_summary:
            notes.append(f"天气提醒：{weather_summary}")
        for meal in meals:
            meal_label = "午餐" if meal.meal_type == "lunch" else "晚餐"
            notes.append(f"{meal_label}选择 {meal.venue_name}，预算约 {meal.estimated_cost:.0f} 元。")
        return notes

    def _arrange_spots_for_schedule(self, spots: list, arrival_segment, departure_segment) -> list:
        if not spots:
            return []
        late_arrival = False
        arrival_minutes = segment_arrival_minutes(arrival_segment)
        if arrival_minutes is not None:
            late_arrival = arrival_minutes >= 11 * 60
        early_departure = False
        departure_minutes = segment_departure_minutes(departure_segment)
        if departure_minutes is not None:
            early_departure = departure_minutes <= 18 * 60 + 30
        priority_maps = {
            "default": {"morning": 0, "flexible": 1, "afternoon": 2, "evening": 3, "night": 4},
            "late": {"afternoon": 0, "flexible": 1, "evening": 2, "night": 3, "morning": 4},
            "early": {"morning": 0, "flexible": 1, "afternoon": 2, "evening": 3, "night": 4},
        }
        mode = "late" if late_arrival else "early" if early_departure else "default"
        ordered = sorted(
            enumerate(spots),
            key=lambda item: (
                priority_maps[mode].get(spot_time_bucket(item[1]), 5),
                item[0],
            ),
        )
        return [spot for _, spot in ordered]

    def _merge_food_candidates(self, primary_foods: list, along_route_foods: list) -> list:
        merged = list(primary_foods)
        seen = {self._food_key(food) for food in primary_foods}
        for food in along_route_foods:
            key = self._food_key(food)
            if key in seen:
                continue
            seen.add(key)
            merged.append(food)
        return merged

    def _food_key(self, food) -> str:
        address = (getattr(food, "address", "") or "").strip().lower()
        district = (getattr(food, "district", "") or "").strip().lower()
        return f"{food.name.strip().lower()}|{address or district}"

    def _clone_foods_with_tag(self, foods: list, tag: str) -> list:
        cloned: list = []
        for food in foods:
            copied = deepcopy(food)
            if tag not in copied.tags:
                copied.tags.append(tag)
            cloned.append(copied)
        return cloned

    def _meal_anchor(self, hotel, spots: list, meal_type: str) -> tuple[float, float] | None:
        if spots:
            if meal_type == "lunch":
                pivot = max(0, len(spots) // 2 - 1)
                return spots[pivot].lat, spots[pivot].lon
            return spots[-1].lat, spots[-1].lon
        if hotel:
            return hotel.lat, hotel.lon
        return None

    def _meal_area_hints(self, hotel, spots: list, meal_type: str) -> list[str]:
        hints: list[str] = []
        if hotel and getattr(hotel, "district", ""):
            hints.append(hotel.district)
        if meal_type == "lunch":
            relevant_spots = spots[: max(1, len(spots) // 2)]
        else:
            relevant_spots = spots[max(0, len(spots) // 2 - 1) :]
        for spot in relevant_spots:
            district = (spot.district or "").strip()
            if district and district not in hints:
                hints.append(district)
        return hints

    def _dedupe_named_foods_by_route(self, foods: list, meal_path: list[list[float]]) -> list:
        grouped: dict[str, list] = {}
        for food in foods:
            grouped.setdefault(food.name.strip().lower(), []).append(food)
        deduped: list = []
        for candidates in grouped.values():
            if len(candidates) == 1:
                deduped.extend(candidates)
                continue
            best = min(candidates, key=lambda food: (self._route_distance_for_food(food, meal_path), self._food_key(food)))
            deduped.append(best)
        return deduped

    def _collect_along_route_foods(self, city_name: str, path: list[list[float]], tastes: list[str]) -> list:
        merged: list = []
        for radius_meters in [800, 1200, 1800, 2500]:
            candidates = self.search_provider.search_along_route_foods(
                city_name,
                path,
                tastes,
                radius_meters=radius_meters,
            )
            merged = self._merge_food_candidates(merged, candidates)
            if len(merged) >= 8:
                break
        return merged

    def _route_distance_for_food(self, food, route_path: list[list[float]]) -> float:
        if not route_path or len(route_path) < 2 or not getattr(food, "lat", 0.0) or not getattr(food, "lon", 0.0):
            return 999.0
        return min(
            self._point_to_segment_distance(food.lat, food.lon, start[1], start[0], end[1], end[0])
            for start, end in zip(route_path, route_path[1:])
        )

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

    def _prepare_hotel_candidates(self, request: TripRequest, profile) -> list:
        hotels = list(profile.hotels)
        area_hint = (request.must_have_hotel_area or "").strip()
        if not area_hint:
            return hotels
        if not hasattr(self.search_provider, "search_hotels"):
            return hotels
        try:
            area_hotels = self.search_provider.search_hotels(profile.city, area_hint)
        except Exception:
            area_hotels = []
        return self._merge_hotels(hotels, area_hotels)

    def _merge_hotels(self, primary_hotels: list, extra_hotels: list) -> list:
        merged = list(primary_hotels)
        seen = {
            f"{hotel.name.strip().lower()}|{(hotel.address or hotel.district or '').strip().lower()}"
            for hotel in primary_hotels
        }
        for hotel in extra_hotels:
            key = f"{hotel.name.strip().lower()}|{(hotel.address or hotel.district or '').strip().lower()}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(hotel)
        return merged

    def _build_budget(self, request: TripRequest, profile, day_plans: list[DayPlan]):
        round_trip_cost, round_trip_note = self.transport.estimate_round_trip_cost(request, profile)
        return self.budget.build_budget(request, profile, day_plans, round_trip_cost, round_trip_note)

    def _build_guide(
        self,
        request,
        constraints,
        profile,
        origin_match,
        destination_match,
        day_plans,
        budget_summary,
        validation_issues,
        mode,
        provider_statuses,
        travel_notes,
        evidence_mode_summary,
    ) -> str:
        llm_text = None
        if self.llm_provider.is_available():
            llm_text = self.llm_provider.write_guide(
                {
                    "request": asdict(request),
                    "constraints": asdict(constraints),
                    "origin_match": asdict(origin_match),
                    "destination_match": asdict(destination_match),
                    "day_plans": [asdict(day) for day in day_plans],
                    "budget_summary": asdict(budget_summary),
                    "validation_issues": [asdict(issue) for issue in validation_issues],
                    "provider_statuses": [asdict(provider) for provider in provider_statuses],
                    "travel_notes": [asdict(note) for note in travel_notes],
                    "evidence_mode_summary": evidence_mode_summary,
                }
            )
        if llm_text:
            return llm_text
        return self.web_guide.build_markdown(
            request,
            constraints,
            profile,
            origin_match,
            destination_match,
            day_plans,
            budget_summary,
            validation_issues,
            mode,
            provider_statuses,
            travel_notes,
            evidence_mode_summary,
        )

    def _build_evidence_summary(self, day_plans, travel_notes) -> str:
        counters = {"网页检索": 0, "攻略聚合": 0, "大模型整理": 0}
        for day in day_plans:
            sources = []
            if day.hotel:
                sources.extend(day.hotel.source_evidence)
            for spot in day.spots:
                sources.extend(spot.source_evidence)
            for meal in day.meals:
                sources.extend(meal.source_evidence)
            for evidence in sources:
                counters[evidence.evidence_type] = counters.get(evidence.evidence_type, 0) + 1
        for note in travel_notes:
            counters[note.evidence_type] = counters.get(note.evidence_type, 0) + 1
        return "；".join(f"{key} {value} 条" for key, value in counters.items() if value > 0) or "当前已确认真实在线数据。"
