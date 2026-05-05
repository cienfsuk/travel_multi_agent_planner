from __future__ import annotations

import math
import re
from collections.abc import Callable
from copy import copy, deepcopy

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
from .models import AgentTraceStep, DayPlan, MealRecommendation, ProviderStatus, RoutePoint, TripPlan, TripRequest
from .providers import BailianLLMProvider, ChinaRailway12306Provider, TencentMapProvider, TencentMapSearchProvider
from .scheduling import (
    DEFAULT_DAY_END_MINUTES,
    DEFAULT_DAY_START_MINUTES,
    INTERCITY_ARRIVAL_BUFFER_MINUTES,
    INTERCITY_DEPARTURE_BUFFER_MINUTES,
    build_transport_nodes,
    segment_arrival_minutes,
    segment_departure_minutes,
    spot_time_bucket,
)


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

        effective_food_tastes = self._effective_food_tastes(request)
        search_request = request
        if effective_food_tastes != list(request.food_tastes or []):
            search_request = copy(request)
            search_request.food_tastes = effective_food_tastes

        profile, origin_match, destination_match, provider_notes = self.search_agent.build_city_profile(search_request, self.search_provider)
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
        summary_markdown = self._append_personalization_summary(summary_markdown, day_plans)
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
        effective_food_tastes = self._effective_food_tastes(request)
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
            personalization_transport_notes = self._apply_additional_note_transport_preferences(
                request,
                daily["day"],
                arrival_segment,
                departure_segment,
            )
            daily["spots"] = self._arrange_spots_for_schedule(
                daily["spots"],
                arrival_segment,
                departure_segment,
                daily["day"],
                request.style,
            )
            daily["spots"] = self._trim_spots_by_day_window(
                daily["spots"],
                arrival_segment,
                departure_segment,
                request.style,
            )
            daily["city_name"] = profile.city
            daily["meal_paths"] = self._build_meal_paths(hotel, daily["spots"])
            daily["meal_candidate_pools"] = {
                "lunch": self._build_meal_candidate_pool(profile.city, hotel, daily["spots"], effective_food_tastes, "lunch", profile.foods, daily["meal_paths"].get("lunch", [])),
                "dinner": self._build_meal_candidate_pool(profile.city, hotel, daily["spots"], effective_food_tastes, "dinner", profile.foods, daily["meal_paths"].get("dinner", [])),
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
            self._apply_additional_note_meal_preferences(request, daily_with_meals, used_food_keys)
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
                + personalization_transport_notes
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
        radii = [600, 1000, 1500, 2200, 3000] if meal_type == "lunch" else [800, 1200, 1800, 2500, 3500]
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
            if len({self._food_key(food) for food in merged}) >= 12:
                break
        for area_hint in area_hints[:3]:
            if len({self._food_key(food) for food in merged}) >= 16:
                break
            merged = self._merge_food_candidates(merged, self.search_provider.search_area_foods(city_name, area_hint, tastes, meal_type))
            if len({self._food_key(food) for food in merged}) >= 16:
                break
        if meal_path:
            merged = self._dedupe_named_foods_by_route(merged, meal_path)
        if merged:
            merged[0].tags.append(f"{meal_label}候选")
        return merged

    def _apply_additional_note_meal_preferences(
        self,
        request: TripRequest,
        daily_plan: dict,
        used_food_keys: set[str] | None = None,
    ) -> None:
        day = int(daily_plan.get("day", 0) or 0)
        preferences = self._extract_day_meal_preferences_v2(request.additional_notes or "", day)
        if not preferences:
            return
        used_keys = used_food_keys if used_food_keys is not None else set()
        meals = list(daily_plan.get("meals") or [])
        pools = daily_plan.get("meal_candidate_pools") or {}
        city_name = str(daily_plan.get("city_name") or request.destination)
        hotel = daily_plan.get("hotel")
        spots = list(daily_plan.get("spots") or [])
        meal_paths = daily_plan.get("meal_paths") or {}
        for preference in preferences:
            meal_type = preference["meal_type"]
            target_index = next((idx for idx, meal in enumerate(meals) if getattr(meal, "meal_type", "") == meal_type), None)
            if target_index is None:
                continue
            current_meal = meals[target_index]
            if self._meal_matches_keywords(current_meal.venue_name, current_meal.cuisine, preference["keywords"]):
                meals[target_index] = self._promote_existing_meal_to_personalized_clean(current_meal, preference["label"])
                note = f"【个性化】已命中第{day}天{self._meal_type_label_clean(meal_type)}偏好：{preference['label']}。"
                plan_notes = daily_plan.setdefault("notes", [])
                if note not in plan_notes:
                    plan_notes.append(note)
                supplemental = self._format_supplemental_food_candidates(
                    list(pools.get(meal_type) or []),
                    current_meal,
                    preference["keywords"],
                )
                if supplemental:
                    supplement_note = (
                        f"【个性化】原真实餐饮候选为：{current_meal.venue_name}。"
                        f" 腾讯API附近个性化候选：{supplemental}。"
                    )
                    if supplement_note not in plan_notes:
                        plan_notes.append(supplement_note)
                continue
            original_candidates = list(pools.get(meal_type) or [])
            original_has_match = any(self._food_candidate_matches(food, preference["keywords"]) for food in original_candidates)
            candidate_pool = self._supplement_personalized_meal_candidates(
                city_name,
                hotel,
                spots,
                meal_type,
                meal_paths.get(meal_type, []),
                list(pools.get(meal_type) or []),
                preference["keywords"],
            )
            pools[meal_type] = candidate_pool
            replacement = None
            fallback_food = None
            for food in candidate_pool:
                if not self._food_candidate_matches(food, preference["keywords"]):
                    continue
                if fallback_food is None:
                    fallback_food = food
                if self._food_key(food) in used_keys:
                    continue
                replacement = self._build_personalized_meal_from_food_clean(
                    food,
                    meal_type,
                    daily_plan,
                    request,
                    preference["label"],
                )
                used_keys.add(self._food_key(food))
                break
            if replacement is None and fallback_food is not None:
                replacement = self._build_personalized_meal_from_food_clean(
                    fallback_food,
                    meal_type,
                    daily_plan,
                    request,
                    preference["label"],
                )
                used_keys.add(self._food_key(fallback_food))
            if replacement is None:
                note = (
                    f"【个性化】未检索到可直接替换为“{preference['label']}”的餐厅，"
                    f"已保留真实餐饮候选：{current_meal.venue_name}。"
                )
                supplemental = self._format_supplemental_food_candidates(
                    candidate_pool,
                    current_meal,
                    preference["keywords"],
                )
                if supplemental:
                    note += f" 腾讯API附近补充候选：{supplemental}。"
                plan_notes = daily_plan.setdefault("notes", [])
                if note not in plan_notes:
                    plan_notes.append(note)
                continue
            meals[target_index] = replacement
            if not original_has_match:
                supplemental = self._format_supplemental_food_candidates(
                    candidate_pool,
                    current_meal,
                    preference["keywords"],
                )
                if supplemental:
                    fallback_note = (
                        f"【个性化】原真实餐饮候选为：{current_meal.venue_name}。"
                        f" 腾讯API附近个性化候选：{supplemental}。"
                    )
                    plan_notes = daily_plan.setdefault("notes", [])
                    if fallback_note not in plan_notes:
                        plan_notes.append(fallback_note)
            note = f"【个性化】已将第{day}天{self._meal_type_label_clean(meal_type)}优先安排为{preference['label']}。"
            plan_notes = daily_plan.setdefault("notes", [])
            if note not in plan_notes:
                plan_notes.append(note)
        daily_plan["meals"] = meals
        daily_plan["meal_candidate_pools"] = pools

    def _format_supplemental_food_candidates(self, candidates: list, current_meal, keywords: list[str], limit: int = 3) -> str:
        current_name = str(getattr(current_meal, "venue_name", "") or "").strip()
        labels: list[str] = []
        seen: set[str] = set()
        for food in list(candidates or []):
            name = str(getattr(food, "name", "") or "").strip()
            if not name or name == current_name or name in seen:
                continue
            if not self._food_candidate_matches(food, keywords):
                continue
            evidence = list(getattr(food, "source_evidence", []) or [])
            if not evidence:
                continue
            seen.add(name)
            cuisine = str(getattr(food, "cuisine", "") or "餐饮").strip()
            district = str(getattr(food, "district", "") or "").strip()
            provider = str(getattr(evidence[0], "provider_label", "") or getattr(evidence[0], "provider", "") or "腾讯位置服务")
            meta = " / ".join(part for part in [cuisine, district, provider] if part)
            labels.append(f"{name}（{meta}）" if meta else name)
            if len(labels) >= limit:
                break
        return "；".join(labels)

    def _effective_food_tastes(self, request: TripRequest) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for raw in list(request.food_tastes or []):
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
        for day in range(1, max(int(request.days or 0), 0) + 1):
            for preference in self._extract_day_meal_preferences_v2(request.additional_notes or "", day):
                for keyword in preference["keywords"]:
                    text = str(keyword or "").strip()
                    if not text or text.isascii():
                        continue
                    key = text.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(text)
        return merged

    def _supplement_personalized_meal_candidates(
        self,
        city_name: str,
        hotel,
        spots: list,
        meal_type: str,
        meal_path: list[list[float]],
        existing_candidates: list,
        keywords: list[str],
    ) -> list:
        if not city_name:
            return existing_candidates

        search_keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
        if not search_keywords:
            return existing_candidates

        merged = list(existing_candidates)
        if meal_path:
            merged = self._merge_food_candidates(
                merged,
                self.search_provider.search_along_route_foods(
                    city_name,
                    meal_path,
                    search_keywords,
                    radius_meters=2500,
                ),
            )
        anchor = self._meal_anchor(hotel, spots, meal_type)
        if anchor is not None:
            for radius_meters in [1200, 1800, 2500, 3500]:
                merged = self._merge_food_candidates(
                    merged,
                    self.search_provider.search_nearby_foods(
                        city_name,
                        anchor[0],
                        anchor[1],
                        search_keywords,
                        radius_meters=radius_meters,
                        meal_type=meal_type,
                    ),
                )
                if len({self._food_key(food) for food in merged}) >= 6:
                    break
        for area_hint in self._meal_area_hints(hotel, spots, meal_type)[:3]:
            merged = self._merge_food_candidates(
                merged,
                self.search_provider.search_area_foods(
                    city_name,
                    area_hint,
                    search_keywords,
                    meal_type,
                ),
            )
            if len({self._food_key(food) for food in merged}) >= 8:
                break
        if meal_path:
            merged = self._dedupe_named_foods_by_route(merged, meal_path)
        return merged

    def _extract_day_meal_preferences_clean(self, notes: str, day: int) -> list[dict]:
        if not notes or day <= 0:
            return []
        day_tokens = {
            1: "第一天",
            2: "第二天",
            3: "第三天",
            4: "第四天",
            5: "第五天",
            6: "第六天",
            7: "第七天",
        }
        day_token = day_tokens.get(day, f"第{day}天")
        preferences: list[dict] = []
        candidates = [
            ("dinner", ["火锅", "hotpot", "huoguo"], ["晚饭吃火锅", "晚餐吃火锅", "晚上想吃火锅", "晚上吃火锅"]),
            ("dinner", ["烧烤", "烤肉", "bbq", "barbecue", "grill"], ["晚饭吃烧烤", "晚餐吃烧烤", "晚上想吃烧烤", "晚上吃烧烤"]),
            ("lunch", ["火锅", "hotpot", "huoguo"], ["午饭吃火锅", "午餐吃火锅", "中午想吃火锅", "中午吃火锅"]),
            ("lunch", ["烧烤", "烤肉", "bbq", "barbecue", "grill"], ["午饭吃烧烤", "午餐吃烧烤", "中午想吃烧烤", "中午吃烧烤"]),
        ]
        for meal_type, keywords, suffixes in candidates:
            for suffix in suffixes:
                phrase = f"{day_token}{suffix}"
                if phrase in notes:
                    preferences.append({"meal_type": meal_type, "label": phrase, "keywords": keywords})
                    break
        return preferences

    def _extract_day_meal_preferences_v2(self, notes: str, day: int) -> list[dict]:
        if not notes or day <= 0:
            return []
        preferences: list[dict] = []
        seen: set[tuple[str, str]] = set()
        clauses = re.split(r"[，。；;,\n]+", str(notes))
        day_tokens = self._day_token_variants_v2(day)
        for raw_clause in clauses:
            clause = str(raw_clause or "").strip()
            if not clause:
                continue
            lowered = clause.lower()
            if not any(token in clause or token in lowered for token in day_tokens):
                continue
            meal_type = self._infer_meal_type_v2(clause)
            for cuisine_label, keywords in self._extract_cuisine_preferences_v2(clause):
                dedupe_key = (meal_type, cuisine_label)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                preferences.append(
                    {
                        "meal_type": meal_type,
                        "label": clause,
                        "cuisine_label": cuisine_label,
                        "keywords": keywords,
                    }
                )
        return preferences

    def _day_token_variants_v2(self, day: int) -> list[str]:
        chinese = {
            1: "第一天",
            2: "第二天",
            3: "第三天",
            4: "第四天",
            5: "第五天",
            6: "第六天",
            7: "第七天",
        }
        return [chinese.get(day, f"第{day}天"), f"第{day}天", f"day{day}", f"day {day}", f"d{day}"]

    def _infer_meal_type_v2(self, clause: str) -> str:
        text = str(clause or "")
        if any(token in text for token in ["午饭", "午餐", "中午"]):
            return "lunch"
        if any(token in text for token in ["晚饭", "晚餐", "晚上", "夜宵", "宵夜"]):
            return "dinner"
        return "dinner"

    def _extract_cuisine_preferences_v2(self, clause: str) -> list[tuple[str, list[str]]]:
        alias_map: list[tuple[str, list[str]]] = [
            ("火锅", ["火锅", "hotpot", "huoguo"]),
            ("烧烤", ["烧烤", "烤肉", "bbq", "barbecue", "grill"]),
            ("海鲜", ["海鲜"]),
            ("日料", ["日料", "寿司", "刺身", "日本料理"]),
            ("韩料", ["韩料", "韩式", "韩国料理", "部队锅"]),
            ("西餐", ["西餐", "牛排", "意面", "披萨", "汉堡"]),
            ("川菜", ["川菜"]),
            ("湘菜", ["湘菜"]),
            ("粤菜", ["粤菜"]),
            ("鲁菜", ["鲁菜"]),
            ("淮扬菜", ["淮扬菜"]),
            ("本帮菜", ["本帮菜"]),
            ("串串", ["串串", "串串香"]),
            ("烤鱼", ["烤鱼"]),
            ("小龙虾", ["小龙虾"]),
            ("咖啡", ["咖啡"]),
            ("甜品", ["甜品", "蛋糕", "冰淇淋"]),
            ("面食", ["面", "面条", "拉面", "刀削面", "面馆"]),
            ("米粉", ["米粉"]),
            ("麻辣烫", ["麻辣烫"]),
            ("麻辣香锅", ["麻辣香锅"]),
            ("饺子", ["饺子"]),
            ("早茶", ["早茶"]),
        ]
        text = str(clause or "")
        found: list[tuple[str, list[str]]] = []
        seen_labels: set[str] = set()
        for label, keywords in alias_map:
            if any(keyword.lower() in text.lower() for keyword in keywords):
                if label not in seen_labels:
                    seen_labels.add(label)
                    found.append((label, keywords))
        pattern = re.compile(r"(?:想吃|想安排|安排|吃|来点|试试|尝尝)([^，。；,]{1,12})")
        for match in pattern.findall(text):
            segment = str(match or "").strip()
            segment = re.sub(r"^(早饭|早餐|早茶|午饭|午餐|中午|晚饭|晚餐|晚上|夜宵|宵夜)", "", segment)
            segment = re.sub(r"(就行|即可|就好|都行|安排一下|安排)$", "", segment).strip()
            for item in re.split(r"(?:和|及|与|、|/|或|或者)", segment):
                cuisine = str(item or "").strip()
                cuisine = cuisine.strip("想吃来点试试尝尝安排一下的吧呀呢")
                if len(cuisine) < 2 or len(cuisine) > 8:
                    continue
                if not re.search(r"[\u4e00-\u9fffA-Za-z]", cuisine):
                    continue
                if cuisine not in seen_labels:
                    seen_labels.add(cuisine)
                    found.append((cuisine, [cuisine]))
        return found

    def _build_personalized_meal_from_food_clean(
        self,
        food,
        meal_type: str,
        daily_plan: dict,
        request: TripRequest,
        label: str,
    ) -> MealRecommendation:
        day_index = int(daily_plan.get("day", 1) or 1)
        estimated_cost = self.food_spot._cost_for_meal(food, meal_type, day_index)
        reason = self.food_spot._build_reason(food, meal_type, request.interests, request.food_tastes)
        reason = f"{reason} 已按“{label}”偏好优先匹配。"
        return MealRecommendation(
            venue_name=food.name,
            meal_type=meal_type,
            estimated_cost=estimated_cost,
            reason=reason,
            venue_district=food.district,
            cuisine=food.cuisine,
            lat=food.lat,
            lon=food.lon,
            anchor_distance_km=0.0,
            route_distance_km=0.0,
            fallback_used=False,
            selection_tier="personalized",
            source_evidence=food.source_evidence[:2],
        )

    def _promote_existing_meal_to_personalized_clean(self, meal, label: str) -> MealRecommendation:
        reason = str(getattr(meal, "reason", "") or "").strip()
        if label not in reason:
            reason = f"{reason} 已按“{label}”偏好命中。".strip()
        return MealRecommendation(
            venue_name=meal.venue_name,
            meal_type=meal.meal_type,
            estimated_cost=meal.estimated_cost,
            reason=reason,
            venue_district=meal.venue_district,
            cuisine=meal.cuisine,
            lat=meal.lat,
            lon=meal.lon,
            anchor_distance_km=meal.anchor_distance_km,
            route_distance_km=meal.route_distance_km,
            fallback_used=meal.fallback_used,
            selection_tier="personalized",
            source_evidence=list(getattr(meal, "source_evidence", []) or []),
        )

    def _build_personalized_meal_placeholder_clean(self, meal_type: str, current_meal, label: str) -> MealRecommendation:
        return MealRecommendation(
            venue_name=f"{label}（待到店确认）",
            meal_type=meal_type,
            estimated_cost=88.0 if meal_type == "dinner" else 48.0,
            reason=f"未检索到明显匹配候选，已为该餐次保留“{label}”占位，请出发前二次确认。",
            venue_district=current_meal.venue_district,
            cuisine=label,
            lat=current_meal.lat,
            lon=current_meal.lon,
            anchor_distance_km=0.0,
            route_distance_km=0.0,
            fallback_used=True,
            selection_tier="personalized-placeholder",
            source_evidence=[],
        )

    def _meal_type_label_clean(self, meal_type: str) -> str:
        return "午餐" if meal_type == "lunch" else "晚餐"

    def _apply_additional_note_transport_preferences(
        self,
        request: TripRequest,
        day: int,
        arrival_segment,
        departure_segment,
    ) -> list[str]:
        min_departure_minutes = self._extract_day_transport_min_departure_clean(request.additional_notes or "", day)
        if min_departure_minutes is None:
            return []
        notes: list[str] = []
        if self._apply_segment_departure_floor_clean(arrival_segment, min_departure_minutes):
            notes.append(f"【个性化】已将第{day}天出发时间调整为{self._format_minutes_clean(min_departure_minutes)}以后。")
        if self._apply_segment_departure_floor_clean(departure_segment, min_departure_minutes):
            notes.append(f"【个性化】已将第{day}天返程时间调整为{self._format_minutes_clean(min_departure_minutes)}以后。")
        return notes

    def _extract_day_transport_min_departure_clean(self, notes: str, day: int) -> int | None:
        if not notes or day <= 0:
            return None
        day_tokens = {
            1: "第一天",
            2: "第二天",
            3: "第三天",
            4: "第四天",
            5: "第五天",
            6: "第六天",
            7: "第七天",
        }
        day_token = day_tokens.get(day, f"第{day}天")
        specific_phrases = [
            f"{day_token}出发别太早",
            f"{day_token}出发不要太早",
            f"{day_token}别太早出发",
            f"{day_token}不要太早出发",
            f"{day_token}不要赶早车",
            f"{day_token}不要赶车太早",
            f"{day_token}晚点出发",
            f"{day_token}晚些出发",
        ]
        if any(phrase in notes for phrase in specific_phrases):
            return 9 * 60
        if day == 1 and any(
            token in notes
            for token in ["出发别太早", "出发不要太早", "别太早出发", "不要太早出发", "不要赶早车", "不要赶车太早", "晚点出发", "晚些出发"]
        ):
            return 9 * 60
        return None

    def _apply_segment_departure_floor_clean(self, segment, min_departure_minutes: int) -> bool:
        if segment is None:
            return False
        depart_minutes = self._time_to_minutes_clean(getattr(segment, "depart_time", ""))
        if depart_minutes is None or depart_minutes >= min_departure_minutes:
            return False
        original_depart = getattr(segment, "depart_time", "") or "待定"
        original_arrive = getattr(segment, "arrive_time", "") or "待定"
        duration_minutes = max(60, int(getattr(segment, "duration_minutes", 90) or 90))
        segment.depart_time = self._format_minutes_clean(min_departure_minutes)
        segment.arrive_time = self._format_minutes_clean(min_departure_minutes + duration_minutes)
        segment.confidence = "personalized"
        segment.description = (
            f"【个性化】已避开过早车次，建议改乘 {segment.depart_time} 以后出发的班次。"
            f" 原始查询参考为 {original_depart} -> {original_arrive}。"
        )
        return True

    def _append_personalization_summary(self, summary_markdown: str, day_plans: list[DayPlan]) -> str:
        meal_lines: list[str] = []
        transport_lines: list[str] = []
        for day in day_plans:
            for meal in day.meals:
                if getattr(meal, "selection_tier", "") in {"personalized", "personalized-placeholder"}:
                    meal_lines.append(
                        f"- 第{day.day}天{self._meal_type_label_clean(meal.meal_type)}：{meal.venue_name}"
                        f" | 菜系：{meal.cuisine or '待确认'}"
                    )
            for note in day.notes:
                if "腾讯API附近补充候选" in note:
                    meal_lines.append(f"- 第{day.day}天补充餐饮候选：{note}")
            if day.arrival_segment is not None and getattr(day.arrival_segment, "confidence", "") == "personalized":
                transport_lines.append(
                    f"- 第{day.day}天入城交通：{day.arrival_segment.depart_time or '待定'} -> "
                    f"{day.arrival_segment.arrive_time or '待定'} | {day.arrival_segment.description}"
                )
            if day.departure_segment is not None and getattr(day.departure_segment, "confidence", "") == "personalized":
                transport_lines.append(
                    f"- 第{day.day}天返程交通：{day.departure_segment.depart_time or '待定'} -> "
                    f"{day.departure_segment.arrive_time or '待定'} | {day.departure_segment.description}"
                )
        updated = str(summary_markdown or "")
        if meal_lines and "## 个性化餐饮落地" not in updated:
            updated += "\n".join(["", "## 个性化餐饮落地", *meal_lines])
        if transport_lines and "## 个性化交通落地" not in updated:
            updated += "\n".join(["", "## 个性化交通落地", *transport_lines])
        return updated

    def _time_to_minutes_clean(self, value: str) -> int | None:
        if not value or ":" not in value:
            return None
        try:
            hour_text, minute_text = value.split(":", 1)
            return int(hour_text) * 60 + int(minute_text)
        except ValueError:
            return None

    def _format_minutes_clean(self, minutes: int) -> str:
        hour = max(0, int(minutes) // 60) % 24
        minute = max(0, int(minutes) % 60)
        return f"{hour:02d}:{minute:02d}"

    def _extract_day_meal_preferences(self, notes: str, day: int) -> list[dict]:
        if not notes or day <= 0:
            return []
        day_tokens = {
            1: "第一天",
            2: "第二天",
            3: "第三天",
            4: "第四天",
            5: "第五天",
            6: "第六天",
            7: "第七天",
        }
        day_token = day_tokens.get(day, f"第{day}天")
        preferences: list[dict] = []
        candidates = [
            ("dinner", ["火锅", "hotpot", "huoguo"], ["晚饭吃火锅", "晚餐吃火锅", "晚上想吃火锅", "晚上吃火锅"]),
            ("dinner", ["烧烤", "烤肉", "bbq", "barbecue", "grill"], ["晚饭吃烧烤", "晚餐吃烧烤", "晚上想吃烧烤", "晚上吃烧烤"]),
            ("lunch", ["火锅", "hotpot", "huoguo"], ["午饭吃火锅", "午餐吃火锅", "中午想吃火锅", "中午吃火锅"]),
            ("lunch", ["烧烤", "烤肉", "bbq", "barbecue", "grill"], ["午饭吃烧烤", "午餐吃烧烤", "中午想吃烧烤", "中午吃烧烤"]),
        ]
        for meal_type, keywords, suffixes in candidates:
            for suffix in suffixes:
                phrase = f"{day_token}{suffix}"
                if phrase in notes:
                    preferences.append({"meal_type": meal_type, "label": phrase, "keywords": keywords})
                    break
        return preferences

    def _meal_matches_keywords(self, venue_name: str, cuisine: str, keywords: list[str]) -> bool:
        text = f"{venue_name or ''} {cuisine or ''}".lower()
        return any(keyword.lower() in text for keyword in keywords)

    def _food_candidate_matches(self, food, keywords: list[str]) -> bool:
        text = f"{getattr(food, 'name', '')} {getattr(food, 'cuisine', '')} {getattr(food, 'description', '')}".lower()
        return any(keyword.lower() in text for keyword in keywords)

    def _trim_spots_by_day_window(
        self,
        spots: list[PointOfInterest],
        arrival_segment,
        departure_segment,
        style: str,
    ) -> list[PointOfInterest]:
        if not spots:
            return spots
        start_minutes = DEFAULT_DAY_START_MINUTES
        end_minutes = DEFAULT_DAY_END_MINUTES
        arrival_minutes = segment_arrival_minutes(arrival_segment)
        departure_minutes = segment_departure_minutes(departure_segment)
        if arrival_minutes is not None:
            start_minutes = max(start_minutes, arrival_minutes + INTERCITY_ARRIVAL_BUFFER_MINUTES)
        if departure_minutes is not None:
            end_minutes = min(end_minutes, departure_minutes - INTERCITY_DEPARTURE_BUFFER_MINUTES)
        available_minutes = max(0, end_minutes - start_minutes)
        minutes_per_spot = {"relaxed": 180, "balanced": 150, "dense": 120}.get(style, 150)
        allowed_spots = max(1, available_minutes // minutes_per_spot) if available_minutes > 0 else 1
        capped = min(len(spots), max(1, allowed_spots))
        if arrival_minutes is not None and arrival_minutes >= 15 * 60:
            capped = 1
        elif arrival_minutes is not None and arrival_minutes >= 12 * 60:
            capped = min(capped, 2)
        if departure_minutes is not None and departure_minutes <= 14 * 60:
            capped = 1
        elif departure_minutes is not None and departure_minutes <= 17 * 60:
            capped = min(capped, 2)
        return list(spots[:capped])

    def _build_personalized_meal_from_food(self, food, meal_type: str, daily_plan: dict, request: TripRequest, label: str) -> MealRecommendation:
        day_index = int(daily_plan.get("day", 1) or 1)
        estimated_cost = self.food_spot._cost_for_meal(food, meal_type, day_index)
        reason = self.food_spot._build_reason(food, meal_type, request.interests, request.food_tastes)
        reason = f"{reason} 已按“{label}”偏好优先匹配。"
        return MealRecommendation(
            venue_name=food.name,
            meal_type=meal_type,
            estimated_cost=estimated_cost,
            reason=reason,
            venue_district=food.district,
            cuisine=food.cuisine,
            lat=food.lat,
            lon=food.lon,
            anchor_distance_km=0.0,
            route_distance_km=0.0,
            fallback_used=False,
            selection_tier="personalized",
            source_evidence=food.source_evidence[:2],
        )

    def _build_personalized_meal_placeholder(self, meal_type: str, current_meal, label: str) -> MealRecommendation:
        return MealRecommendation(
            venue_name=f"{label}（待到店确认）",
            meal_type=meal_type,
            estimated_cost=88.0 if meal_type == "dinner" else 48.0,
            reason=f"未检索到明显匹配候选，已为该餐次保留“{label}”占位，请出发前二次确认。",
            venue_district=current_meal.venue_district,
            cuisine=label,
            lat=current_meal.lat,
            lon=current_meal.lon,
            anchor_distance_km=0.0,
            route_distance_km=0.0,
            fallback_used=True,
            selection_tier="personalized-placeholder",
            source_evidence=[],
        )

    def _meal_type_label(self, meal_type: str) -> str:
        return "午餐" if meal_type == "lunch" else "晚餐"

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

    def _arrange_spots_for_schedule(self, spots: list, arrival_segment, departure_segment, day: int = 1, style: str = "balanced") -> list:
        if not spots:
            return []
        late_arrival = False
        arrival_minutes = segment_arrival_minutes(arrival_segment)
        if arrival_minutes is not None:
            late_arrival = arrival_minutes >= 11 * 60  # 11:00
        early_departure = False
        departure_minutes = segment_departure_minutes(departure_segment)
        if departure_minutes is not None:
            early_departure = departure_minutes <= 18 * 60 + 30

        # Base slots per day by style
        base_slots = {"relaxed": 2, "balanced": 3, "dense": 4}.get(style, 3)

        # Reduce spots for late arrival on day 1 (e.g., arriving at noon means only afternoon/evening available)
        max_spots = base_slots
        if late_arrival and day == 1:
            max_spots = 2  # Only afternoon + evening slots available
        elif early_departure and day == 1:
            max_spots = 2  # Only morning slots available before early departure

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
        return [spot for _, spot in ordered][:max_spots]

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
