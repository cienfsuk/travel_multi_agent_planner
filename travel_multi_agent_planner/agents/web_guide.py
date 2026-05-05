from __future__ import annotations

from ..models import (
    BudgetSummary,
    CityMatch,
    CityProfile,
    DayPlan,
    ProviderStatus,
    TravelConstraints,
    TravelNote,
    TripRequest,
    ValidationIssue,
)


INTEREST_LABELS = {
    "culture": "文化",
    "food": "美食",
    "nature": "自然",
    "history": "历史",
    "photography": "摄影",
    "shopping": "购物",
    "tea": "茶文化",
    "night": "夜游",
    "relaxed": "慢生活",
    "museum": "博物馆",
    "art": "艺术",
    "architecture": "建筑",
    "park": "公园",
    "landmark": "地标",
}

STYLE_LABELS = {
    "relaxed": "轻松慢游",
    "balanced": "均衡舒展",
    "dense": "高密度打卡",
}

SEGMENT_LABELS = {
    "intercity": "城际交通",
    "taxi": "打车",
    "metro": "地铁",
    "bus": "公交",
    "walk": "步行",
}


class WebGuideAgent:
    def build_markdown(
        self,
        request: TripRequest,
        constraints: TravelConstraints,
        profile: CityProfile,
        origin_match: CityMatch,
        destination_match: CityMatch,
        day_plans: list[DayPlan],
        budget_summary: BudgetSummary,
        validation_issues: list[ValidationIssue],
        mode: str,
        provider_statuses: list[ProviderStatus],
        travel_notes: list[TravelNote] | None = None,
        evidence_mode_summary: str = "",
    ) -> str:
        lines = [
            f"# {profile.city} {request.days} 天青年旅行手册",
            "",
            "## 基本信息",
            f"- 出发地：{request.origin}",
            f"- 目的地：{profile.city}",
            f"- 天数：{request.days}",
            f"- 同行人数：{request.traveler_count}",
            f"- 总预算：{request.budget:.0f} 元",
            f"- 兴趣偏好：{self._join_interest_labels(request.interests)}",
            f"- 口味偏好：{self._join_text(request.food_tastes, '未指定')}",
            f"- 出游节奏：{STYLE_LABELS.get(request.style, request.style)}",
            f"- 规划模式：{'在线实时规划' if mode == 'online' else '离线兜底规划'}",
            f"- 约束上限：每天最多 {constraints.max_daily_spots} 个景点，总预算上限 {constraints.max_total_budget:.0f} 元",
            "",
            "## 本次个性化与偏好",
        ]

        lines.extend(self._build_personalization_lines(request, constraints))
        lines.extend(
            [
                "",
                "## 城市确认",
                f"- 出发地识别：{origin_match.input_name} -> {origin_match.confirmed_name}（{origin_match.region}）",
                f"- 目的地识别：{destination_match.input_name} -> {destination_match.confirmed_name}（{destination_match.region}）",
                f"- 数据来源：{destination_match.provider}",
                "",
                "## 城市概览",
                profile.intro,
                "",
                "## 证据说明",
                f"- {evidence_mode_summary or '当前结果基于实时检索到的景点、餐饮、酒店与交通数据生成。'}",
                "",
                "## 模块状态",
            ]
        )

        for provider in provider_statuses:
            lines.append(f"- {provider.name}：{'可用' if provider.active else '不可用'} | {provider.detail}")

        if travel_notes:
            lines.extend(["", "## 攻略摘要"])
            for note in travel_notes:
                lines.append(f"- {note.title} | {note.style_tag} | {note.summary}")

        lines.extend(self._build_intercity_section(day_plans))
        lines.extend(["", "## 每日行程"])

        for day in day_plans:
            lines.append(f"### 第 {day.day} 天：{day.theme}")
            if day.hotel:
                hotel_provider = self._provider_label(day.hotel.source_evidence)
                lines.append(
                    f"- 酒店：{day.hotel.name} | 区域：{day.hotel.district} | 参考价 {day.hotel.price_per_night:.0f} 元/晚 | 来源：{hotel_provider}"
                )
            for index, spot in enumerate(day.spots, start=1):
                provider = self._provider_label(spot.source_evidence)
                visit_window = spot.estimated_visit_window or spot.best_time or "待定"
                lines.append(
                    f"- 景点 {index}：{spot.name} | 区域：{spot.district} | 建议时段：{visit_window} | 门票：{spot.ticket_cost:.0f} 元 | 来源：{provider}"
                )
            for meal in day.meals:
                provider = self._provider_label(meal.source_evidence)
                meal_label = "午餐" if meal.meal_type == "lunch" else "晚餐"
                lines.append(
                    f"- {meal_label}：{meal.venue_name} | 菜系：{meal.cuisine or '待确认'} | 预计 {meal.estimated_cost:.0f} 元 | 理由：{meal.reason} | 来源：{provider}"
                )
            for segment in day.transport_segments:
                lines.append(
                    f"- {SEGMENT_LABELS.get(segment.segment_type, segment.segment_type)}：{segment.from_label} -> {segment.to_label} | 约 {segment.duration_minutes} 分钟 | {segment.estimated_cost:.0f} 元 | {segment.description}"
                )
            for note in day.notes:
                lines.append(f"- 备注：{note}")
            lines.append("")

        lines.extend(
            [
                "## 预算总览",
                f"- 总估算：{budget_summary.total_estimated:.0f} 元",
                f"- 预算结余：{budget_summary.remaining_budget:.0f} 元",
                f"- 是否预算内：{'是' if budget_summary.is_within_budget else '否'}",
                "",
            ]
        )
        for line in budget_summary.lines:
            lines.append(f"- {line.category}：{line.amount:.0f} 元 | {line.note}")

        lines.extend(["", "## 约束校验"])
        if validation_issues:
            for issue in validation_issues:
                day_text = f"第 {issue.day} 天" if issue.day else "全局"
                lines.append(f"- [{issue.severity}] {day_text} {issue.category}：{issue.message} | 建议：{issue.suggested_fix}")
        else:
            lines.append("- 未发现明显约束冲突。")
        return "\n".join(lines)

    def _build_personalization_lines(self, request: TripRequest, constraints: TravelConstraints) -> list[str]:
        lines = [
            f"- 兴趣偏好已按中文展示：{self._join_interest_labels(request.interests)}",
            f"- 口味偏好：{self._join_text(request.food_tastes, '未指定')}",
            f"- 节奏偏好：{STYLE_LABELS.get(request.style, request.style)}",
        ]
        if request.preferred_areas:
            lines.append(f"- 偏好区域：{'、'.join(request.preferred_areas)}")
        if request.must_have_hotel_area:
            lines.append(f"- 酒店区域偏好：{request.must_have_hotel_area}")
        if request.additional_notes:
            lines.append(f"- 补充要求：{request.additional_notes}")
        if constraints.must_include_spots:
            lines.append(f"- 必达点位：{'、'.join(constraints.must_include_spots)}")
        for day, spots in sorted(constraints.must_include_spots_by_day.items()):
            if spots:
                lines.append(f"- 第 {day} 天必须覆盖：{'、'.join(spots)}")
        if len(lines) == 3:
            lines.append("- 本次未额外添加文本型个性化要求。")
        return lines

    def _build_intercity_section(self, day_plans: list[DayPlan]) -> list[str]:
        lines = ["", "## 城际交通"]
        first_day = day_plans[0] if day_plans else None
        last_day = day_plans[-1] if day_plans else None
        if first_day and first_day.arrival_segment:
            arrival = first_day.arrival_segment
            lines.append(
                f"- 去程：{arrival.transport_code or '待确认'} | {arrival.depart_time or '待定'} -> {arrival.arrive_time or '待定'} | {arrival.description}"
            )
        if last_day and last_day.departure_segment:
            departure = last_day.departure_segment
            lines.append(
                f"- 返程：{departure.transport_code or '待确认'} | {departure.depart_time or '待定'} -> {departure.arrive_time or '待定'} | {departure.description}"
            )
        if len(lines) == 2:
            lines.append("- 当前案例未生成独立的城际交通段。")
        return lines

    def _join_interest_labels(self, interests: list[str]) -> str:
        if not interests:
            return "未指定"
        return "、".join(INTEREST_LABELS.get(item, item) for item in interests)

    def _join_text(self, values: list[str], fallback: str) -> str:
        return "、".join(value for value in values if value) if values else fallback

    def _provider_label(self, evidence_items: list) -> str:
        if not evidence_items:
            return "未知来源"
        first = evidence_items[0]
        return first.provider_label or first.provider
