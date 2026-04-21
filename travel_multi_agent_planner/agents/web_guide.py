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
            f"# {profile.city} {request.days} 天智能旅行手册",
            "",
            "## 基本信息",
            f"- 出发地：{request.origin}",
            f"- 目的地：{profile.city}",
            f"- 天数：{request.days}",
            f"- 总预算：{request.budget:.0f} 元",
            f"- 同行人数：{request.traveler_count}",
            f"- 兴趣偏好：{', '.join(request.interests)}",
            f"- 口味偏好：{', '.join(request.food_tastes) if request.food_tastes else '未指定'}",
            f"- 运行模式：{'在线优先' if mode == 'online' else '离线回退'}",
            f"- 约束：每日最多 {constraints.max_daily_spots} 个景点，预算上限 {constraints.max_total_budget:.0f} 元",
            "",
            "## 城市确认结果",
            f"- 出发地输入：{origin_match.input_name} -> {origin_match.confirmed_name}（{origin_match.region}）",
            f"- 目的地输入：{destination_match.input_name} -> {destination_match.confirmed_name}（{destination_match.region}）",
            f"- 数据来源：{destination_match.provider}",
            "",
            "## 城市概览",
            profile.intro,
            "",
            "## 证据说明",
            f"- {evidence_mode_summary or '当前结果全部来自真实在线检索与整理。'}",
            "",
            "## 模块状态",
        ]

        for provider in provider_statuses:
            lines.append(f"- {provider.name}：{'可用' if provider.active else '不可用'} | {provider.detail}")

        if travel_notes:
            lines.extend(["", "## 攻略聚合摘要"])
            for note in travel_notes:
                lines.append(f"- {note.title} | {note.style_tag} | {note.summary}")

        lines.extend(["", "## 每日行程"])
        for day in day_plans:
            lines.append(f"### 第 {day.day} 天：{day.theme}")
            if day.hotel:
                provider = day.hotel.source_evidence[0].provider_label or day.hotel.source_evidence[0].provider
                lines.append(
                    f"- 酒店：{day.hotel.name} | 区域：{day.hotel.district} | 参考价 {day.hotel.price_per_night:.0f} 元/晚 | 来源 {provider}"
                )
            for spot in day.spots:
                provider = spot.source_evidence[0].provider_label or spot.source_evidence[0].provider
                lines.append(
                    f"- 景点：{spot.name} | 区域：{spot.district} | 建议时段 {spot.estimated_visit_window or spot.best_time} | 门票 {spot.ticket_cost:.0f} 元 | 来源 {provider}"
                )
            for meal in day.meals:
                provider = meal.source_evidence[0].provider_label or meal.source_evidence[0].provider
                meal_label = "午餐" if meal.meal_type == "lunch" else "晚餐"
                lines.append(
                    f"- {meal_label}：{meal.venue_name} | 菜系：{meal.cuisine} | 预计 {meal.estimated_cost:.0f} 元 | 来源 {provider}"
                )
            for segment in day.transport_segments:
                lines.append(
                    f"- 交通：{segment.from_label} -> {segment.to_label} | {segment.segment_type} | {segment.duration_minutes} 分钟 | {segment.estimated_cost:.0f} 元 | {segment.description}"
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
