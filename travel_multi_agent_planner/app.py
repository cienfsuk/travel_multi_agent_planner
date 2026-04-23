from __future__ import annotations

import json
import math
import time
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components

from .models import (
    AnimationBundle,
    AnimationNode,
    AnimationSegment,
    AnimationStep,
    MapNode,
    MapSegment,
    MapViewModel,
    TripPlan,
    TripRequest,
)
from .orchestrator import TravelPlanningOrchestrator
from .persistence import (
    build_case_id,
    case_requires_rebuild,
    list_saved_cases,
    load_case_record,
    load_latest_case,
    load_player_html,
    rebuild_case_assets,
    save_case,
)
from .scheduling import (
    build_day_timeline as scheduling_build_day_timeline,
    build_scheduled_day_timeline as scheduling_build_scheduled_day_timeline,
    format_minutes as scheduling_format_minutes,
    node_duration_minutes as scheduling_node_duration_minutes,
)


STYLE_OPTIONS = {"轻松": "relaxed", "均衡": "balanced", "紧凑": "dense"}
FOOD_BUDGET_OPTIONS = {"省钱": "budget", "均衡": "balanced", "品质优先": "premium"}
HOTEL_BUDGET_OPTIONS = {"经济型": "budget", "舒适型": "balanced", "品质型": "premium"}
INTEREST_OPTIONS = {
    "文化": "culture",
    "美食": "food",
    "自然": "nature",
    "历史": "history",
    "摄影": "photography",
    "购物": "shopping",
    "茶文化": "tea",
    "夜游": "night",
    "慢生活": "relaxed",
}
TASTE_OPTIONS = ["酸", "甜", "苦", "辣", "鲜", "清淡"]
POINT_COLORS = {"hotel": "#c2410c", "spot": "#2563eb", "lunch": "#16a34a", "dinner": "#dc2626"}
DAY_ROUTE_COLORS = ["#2563eb", "#f97316", "#0f766e", "#7c3aed", "#dc2626", "#0891b2", "#84cc16"]
FRAME_STEP = 12


def main() -> None:
    st.set_page_config(page_title="游策旅行规划系统", page_icon="✈️", layout="wide")
    orchestrator = TravelPlanningOrchestrator()
    _ensure_session_defaults()
    _autload_latest_case_once(orchestrator)

    st.title("游策旅行规划系统")
    st.caption("基于百炼大模型与腾讯位置服务的在线增强型多智能体城市深度游规划系统")

    provider_statuses = orchestrator.provider_statuses()
    online_ready = provider_statuses[1].active and provider_statuses[2].active
    if online_ready:
        st.success("当前模式：在线数据已确认。系统会先生成方案并保存本地动画包，再加载案例进行演示。")
    else:
        st.error("当前状态：在线数据配置不完整，无法生成可靠方案。请先配置腾讯位置服务与百炼。")

    status_cols = st.columns(len(provider_statuses))
    for col, status in zip(status_cols, provider_statuses):
        col.metric(status.name, "可用" if status.active else "缺失", status.detail)

    st.info("推荐答辩演示输入：上海 -> 南京，3 天，1500 元，兴趣选择文化 / 美食 / 自然。")
    _render_entry_section(orchestrator)

    plan = st.session_state.get("loaded_plan")
    animation = st.session_state.get("loaded_animation")
    player_html = st.session_state.get("loaded_player_html", "")
    latest_request_summary = st.session_state.get("latest_request_summary")
    loaded_case_id = st.session_state.get("loaded_case_id")
    if plan is None or animation is None:
        st.subheader("系统亮点")
        st.markdown(
            """
            - `Requirement Agent`：抽取用户偏好与结构化约束
            - `Search Agent`：基于腾讯位置服务确认城市并检索真实景点 / 餐饮 / 酒店
            - `Travel Notes Agent`：基于真实在线点位整理攻略摘要
            - `Hotel Agent`：为每日路线匹配酒店
            - `Transport Agent`：细化高铁 / 飞机 / 地铁 / 公交 / 打车 / 步行
            - `Constraint Validator Agent`：校验预算、密度、来源证据与地图完整度
            - `Web Guide Agent`：输出答辩可展示页面和旅行手册
            - `Animation Player`：将生成结果写入本地 JSON 播放包，重复演示时不再调用模型和腾讯检索
            """
        )
        return

    if latest_request_summary:
        st.caption(
            f"当前案例：{latest_request_summary['origin']} -> {latest_request_summary['destination']}，"
            f"{latest_request_summary['days']} 天，预算 {latest_request_summary['budget']:.0f} 元。"
            f"{' | 案例ID：' + loaded_case_id if loaded_case_id else ''}"
        )
    if st.session_state.get("case_refresh_notice"):
        st.info(st.session_state["case_refresh_notice"])
        st.session_state["case_refresh_notice"] = ""

    for warning in plan.warnings[:5]:
        st.warning(warning)

    col1, col2, col3, col4 = st.columns(4)
    budget_value = latest_request_summary["budget"] if latest_request_summary else plan.budget_summary.total_estimated
    col1.metric("总预算", f"{budget_value:.0f} 元")
    col2.metric("预计花费", f"{plan.budget_summary.total_estimated:.0f} 元")
    col3.metric("预算结余", f"{plan.budget_summary.remaining_budget:.0f} 元")
    col4.metric("旅行评分", f"{plan.final_score:.0f}", "已自动修正" if plan.was_revised else "未触发修正")

    tabs = st.tabs(
        [
            "模式状态区",
            "城市确认区",
            "来往交通区",
            "每日路线区",
            "地图与路线区",
            "攻略摘要区",
            "预算分析区",
            "证据与来源区",
            "校验与修正区",
            "Agent 轨迹区",
            "导出区",
        ]
    )

    with tabs[0]:
        st.subheader("模式状态区")
        st.write("当前运行模式：`在线优先 + 预生成动画包`")
        st.markdown(f"- 当前案例：`{animation.case_id}`")
        st.markdown(f"- 证据结构：{plan.evidence_mode_summary or '已确认真实在线数据'}")
        if plan.request.departure_date:
            st.markdown(f"- 出发日期：`{plan.request.departure_date}`")
        provider_df = pd.DataFrame([status.__dict__ for status in plan.provider_statuses])
        provider_df.columns = ["组件", "是否可用", "说明"]
        provider_df["是否可用"] = provider_df["是否可用"].map(lambda x: "可用" if x else "缺失")
        st.dataframe(provider_df, use_container_width=True)
        if plan.search_notes:
            st.markdown("**搜索说明**")
            for note in plan.search_notes:
                st.markdown(f"- {note}")

    with tabs[1]:
        st.subheader("城市确认区")
        city_df = pd.DataFrame(
            [
                {"输入": plan.origin_match.input_name, "确认城市": plan.origin_match.confirmed_name, "行政区": plan.origin_match.region, "国家": plan.origin_match.country, "来源": plan.origin_match.provider},
                {"输入": plan.destination_match.input_name, "确认城市": plan.destination_match.confirmed_name, "行政区": plan.destination_match.region, "国家": plan.destination_match.country, "来源": plan.destination_match.provider},
            ]
        )
        st.dataframe(city_df, use_container_width=True)

    with tabs[2]:
        st.subheader("来往交通区")
        round_trip_cost = next((line.amount for line in plan.budget_summary.lines if line.category == "城际交通"), 0.0)
        round_trip_note = next((line.note for line in plan.budget_summary.lines if line.category == "城际交通"), "")
        outbound_col, inbound_col = st.columns(2)
        with outbound_col:
            st.markdown("**出发 -> 目的地**")
            if plan.day_plans and plan.day_plans[0].arrival_segment:
                segment = plan.day_plans[0].arrival_segment
                st.markdown(f"- 方式：{_segment_label(segment.segment_type)}")
                if segment.transport_code:
                    st.markdown(f"- 车次：{segment.transport_code}")
                st.markdown(f"- 路线：{segment.from_label} -> {segment.to_label}")
                if segment.depart_time and segment.arrive_time:
                    st.markdown(f"- 发到时间：{segment.depart_time} -> {segment.arrive_time}")
                st.markdown(f"- 预计耗时：{segment.duration_minutes} 分钟")
                st.markdown(f"- 预计费用：{segment.estimated_cost:.0f} 元")
                if segment.queried_at:
                    st.markdown(f"- 查询时间：{segment.queried_at}")
                if segment.source_url:
                    st.markdown(f"- 来源：[{segment.source_name or '官方链接'}]({segment.source_url})")
                if segment.confidence != "queried":
                    st.caption("当前为估算回退，不是实时票务结果。")
                st.caption(segment.description)
        with inbound_col:
            st.markdown("**目的地 -> 出发地**")
            if plan.day_plans and plan.day_plans[-1].departure_segment:
                segment = plan.day_plans[-1].departure_segment
                st.markdown(f"- 方式：{_segment_label(segment.segment_type)}")
                if segment.transport_code:
                    st.markdown(f"- 车次：{segment.transport_code}")
                st.markdown(f"- 路线：{segment.from_label} -> {segment.to_label}")
                if segment.depart_time and segment.arrive_time:
                    st.markdown(f"- 发到时间：{segment.depart_time} -> {segment.arrive_time}")
                st.markdown(f"- 预计耗时：{segment.duration_minutes} 分钟")
                st.markdown(f"- 预计费用：{segment.estimated_cost:.0f} 元")
                if segment.queried_at:
                    st.markdown(f"- 查询时间：{segment.queried_at}")
                if segment.source_url:
                    st.markdown(f"- 来源：[{segment.source_name or '官方链接'}]({segment.source_url})")
                if segment.confidence != "queried":
                    st.caption("当前为估算回退，不是实时票务结果。")
                st.caption(segment.description)
        st.markdown(f"**来往交通总预算**：{round_trip_cost:.0f} 元")
        if round_trip_note:
            st.caption(round_trip_note)

    with tabs[3]:
        st.subheader("每日路线区")
        for day in plan.day_plans:
            with st.expander(f"第 {day.day} 天 | {day.theme}", expanded=True):
                if day.hotel:
                    hotel_provider = _provider_label(day.hotel.source_evidence[0]) if day.hotel.source_evidence else "腾讯位置服务"
                    st.markdown(f"**酒店安排**：{day.hotel.name} | 区域：{day.hotel.district} | 参考价 {day.hotel.price_per_night:.0f} 元/晚 | 来源 `{hotel_provider}`")
                    st.caption(day.hotel.description)
                st.markdown("**景点安排**")
                schedule_rows = _build_scheduled_day_timeline(day)
                schedule_map = {(row["kind"], row["name"]): row for row in schedule_rows}
                for index, spot in enumerate(day.spots, start=1):
                    provider = _provider_label(spot.source_evidence[0]) if spot.source_evidence else "腾讯位置服务"
                    schedule = schedule_map.get(("spot", spot.name))
                    schedule_text = f"{schedule['start_time']} - {schedule['end_time']}（停留 {schedule['duration_minutes']} 分钟）" if schedule else (spot.estimated_visit_window or spot.best_time)
                    st.markdown(f"- 第 {index} 站：**{spot.name}** | 类别：{spot.category} | 区域：{spot.district} | 时段 {schedule_text} | 门票 {spot.ticket_cost:.0f} 元 | 来源 `{provider}`")
                    st.caption(spot.description)
                st.markdown("**餐饮安排**")
                for meal in day.meals:
                    provider = _provider_label(meal.source_evidence[0]) if meal.source_evidence else "腾讯位置服务"
                    meal_label = "午餐" if meal.meal_type == "lunch" else "晚餐"
                    schedule = schedule_map.get((meal.meal_type, meal.venue_name))
                    schedule_text = f"{schedule['start_time']} - {schedule['end_time']}（用餐 {schedule['duration_minutes']} 分钟）" if schedule else "用餐时间待定"
                    distance_text = f" | 距主路线约 {meal.route_distance_km:.1f} km" if meal.route_distance_km else ""
                    tier_text = f" | 候选层级：{meal.selection_tier}" if meal.selection_tier else ""
                    st.markdown(f"- {meal_label}：**{meal.venue_name}** | 菜系：{meal.cuisine} | 预计 {meal.estimated_cost:.0f} 元 | 时间 {schedule_text} | 区域：{meal.venue_district}{distance_text}{tier_text} | 来源 `{provider}`")
                    st.caption(meal.reason)
                st.markdown("**时间安排表**")
                st.dataframe(pd.DataFrame(schedule_rows), use_container_width=True)
                st.markdown("**交通分段**")
                if day.arrival_segment:
                    st.markdown(f"- 入城交通：{_segment_label(day.arrival_segment.segment_type)} | {day.arrival_segment.description} | 约 {day.arrival_segment.duration_minutes} 分钟 | {day.arrival_segment.estimated_cost:.0f} 元")
                if day.departure_segment:
                    st.markdown(f"- 返程交通：{_segment_label(day.departure_segment.segment_type)} | {day.departure_segment.description} | 约 {day.departure_segment.duration_minutes} 分钟 | {day.departure_segment.estimated_cost:.0f} 元")
                for segment in day.transport_segments:
                    st.markdown(f"- {_segment_label(segment.segment_type)}：{segment.from_label} -> {segment.to_label} | 约 {segment.duration_minutes} 分钟 | {segment.estimated_cost:.0f} 元 | {segment.distance_km:.1f} km")
                    st.caption(segment.description)
                st.markdown("**执行备注**")
                for note in day.notes:
                    st.markdown(f"- {note}")

    with tabs[4]:
        st.subheader("地图与路线区")
        _render_route_map(
            animation,
            player_html,
            orchestrator.config.tencent_map_server_key or "",
        )

    with tabs[5]:
        st.subheader("攻略摘要区")
        if plan.travel_notes:
            for note in plan.travel_notes:
                st.markdown(f"**{note.title}**")
                st.markdown(f"- 风格：{note.style_tag}")
                st.markdown(f"- 证据类型：{note.evidence_type}")
                st.markdown(f"- 来源：[{note.provider}]({note.source_url})" if note.source_url else f"- 来源：{note.provider}")
                st.caption(note.summary)
        else:
            st.info("当前没有额外攻略摘要。")

    with tabs[6]:
        st.subheader("预算分析区")
        budget_df = pd.DataFrame([line.__dict__ for line in plan.budget_summary.lines])
        budget_df.columns = ["类别", "金额", "说明"]
        st.dataframe(budget_df, use_container_width=True)
        st.bar_chart(budget_df.set_index("类别")["金额"])
        st.markdown(f"预算校验结果：**{'预算内' if plan.budget_summary.is_within_budget else '超预算'}**，结余 {plan.budget_summary.remaining_budget:.0f} 元。")
        st.caption("总预算已包含出发地与目的地往返的城际交通费用。")

    with tabs[7]:
        st.subheader("证据与来源区")
        evidence_rows: list[dict] = []
        for day in plan.day_plans:
            if day.hotel:
                for evidence in day.hotel.source_evidence:
                    evidence_rows.append(_evidence_row(day.day, "酒店", day.hotel.name, evidence))
            for spot in day.spots:
                for evidence in spot.source_evidence:
                    evidence_rows.append(_evidence_row(day.day, "景点", spot.name, evidence))
            for meal in day.meals:
                for evidence in meal.source_evidence:
                    evidence_rows.append(_evidence_row(day.day, "餐饮", meal.venue_name, evidence))
        for note in plan.travel_notes:
            evidence_rows.append({"分组": "攻略", "天数": None, "对象类型": "攻略摘要", "名称": note.title, "证据类型": note.evidence_type, "来源别名": note.provider, "来源提供方": note.provider, "标题": note.title, "摘要": note.summary, "链接": note.source_url})
        if evidence_rows:
            evidence_df = pd.DataFrame(evidence_rows)
            if "分组" not in evidence_df.columns:
                evidence_df["分组"] = evidence_df["天数"].map(lambda day: f"第 {day} 天" if pd.notna(day) else "攻略")
            evidence_df["天数"] = pd.array(evidence_df["天数"], dtype="Int64")
            evidence_df = evidence_df[["分组", "天数", "对象类型", "名称", "证据类型", "来源别名", "来源提供方", "标题", "摘要", "链接"]]
            st.dataframe(evidence_df, use_container_width=True)
        else:
            st.info("当前没有来源证据。")

    with tabs[8]:
        st.subheader("校验与修正区")
        st.markdown(f"- 最终评分：**{plan.final_score:.0f}**")
        st.markdown(f"- 是否触发自动修正：**{'是' if plan.was_revised else '否'}**")
        if plan.validation_issues:
            issues_df = pd.DataFrame([issue.__dict__ for issue in plan.validation_issues])
            issues_df.columns = ["严重程度", "类别", "问题说明", "对应天数", "修正建议"]
            st.dataframe(issues_df, use_container_width=True)
        else:
            st.success("未发现明显约束冲突。")

    with tabs[9]:
        st.subheader("Agent 轨迹区")
        trace_df = pd.DataFrame([{"Agent": step.agent_name, "状态": step.status, "输入摘要": step.input_summary, "输出摘要": step.output_summary, "关键决策": " | ".join(step.key_decisions)} for step in plan.trace])
        trace_df["状态"] = trace_df["状态"].replace({"ok": "正常", "fallback": "局部回退", "warning": "告警"})
        st.dataframe(trace_df, use_container_width=True)

    with tabs[10]:
        st.subheader("导出区")
        plan_json = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)
        animation_json = json.dumps(animation.to_dict(), ensure_ascii=False, indent=2)
        safe_city = (plan.city_profile.city or "trip").replace(" ", "_").lower()
        st.download_button("下载 JSON 结果", plan_json, file_name=f"{safe_city}_trip_plan.json", mime="application/json")
        st.download_button("下载动画播放包", animation_json, file_name=f"{safe_city}_animation_bundle.json", mime="application/json")
        if player_html:
            st.download_button("下载独立 HTML 播放页", player_html, file_name=f"{safe_city}_player.html", mime="text/html")
        st.download_button("下载 Markdown 旅行手册", plan.summary_markdown, file_name=f"{safe_city}_trip_guide.md", mime="text/markdown")
        st.code(plan.summary_markdown, language="markdown")


def _ensure_session_defaults() -> None:
    defaults = {
        "loaded_case_id": None,
        "loaded_plan": None,
        "loaded_animation": None,
        "loaded_player_html": "",
        "latest_request_summary": None,
        "map_day_filter": "全部",
        "map_play_mode": "静态全览",
        "map_frame_index": 0,
        "map_frame_slider": 0,
        "map_is_playing": False,
        "map_view_mode": "腾讯JS播放器",
        "map_scope_signature": "",
        "selected_case_option": None,
        "autoload_checked": False,
        "case_refresh_notice": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _autload_latest_case_once(orchestrator: TravelPlanningOrchestrator) -> None:
    if st.session_state.get("autoload_checked"):
        return
    st.session_state["autoload_checked"] = True
    if st.session_state.get("loaded_plan") is not None:
        return
    latest = load_latest_case()
    if latest:
        _, _, record = latest
        plan, animation, record, notice = _load_saved_case_with_rebuild(selected_record=record, orchestrator=orchestrator)
        _set_loaded_case(plan, animation, record.case_id, load_player_html(record))
        st.session_state["case_refresh_notice"] = notice


def _render_entry_section(orchestrator: TravelPlanningOrchestrator) -> None:
    left_col, right_col = st.columns([1.05, 1.45], gap="large")
    case_records = list_saved_cases()
    with left_col:
        st.subheader("加载已生成案例")
        st.caption("优先从本地播放包加载案例，避免重复调用模型和腾讯接口。")
        if case_records:
            labels = [f"{record.summary} | {record.case_id}" for record in case_records]
            if st.session_state.get("selected_case_option") not in labels:
                st.session_state["selected_case_option"] = labels[0]
            selected_label = st.selectbox("本地案例列表", labels, key="selected_case_option")
            selected_record = case_records[labels.index(selected_label)]
            if st.button("加载选中案例", use_container_width=True, key="load_saved_case"):
                plan, animation, record, notice = _load_saved_case_with_rebuild(selected_record, orchestrator)
                _set_loaded_case(plan, animation, record.case_id, load_player_html(record))
                st.session_state["case_refresh_notice"] = notice
                st.success(f"已加载案例：{record.summary}")
        else:
            st.info("当前还没有已保存案例，请先在右侧重新生成一次。")
        latest = load_latest_case()
        if latest:
            _, _, record = latest
            st.caption(f"最近一次案例：{record.summary}")
            if st.button("加载最近一次案例", use_container_width=True, key="load_latest_case"):
                plan, animation, latest_record, notice = _load_saved_case_with_rebuild(record, orchestrator)
                _set_loaded_case(plan, animation, latest_record.case_id, load_player_html(latest_record))
                st.session_state["case_refresh_notice"] = notice
                st.success(f"已加载最近案例：{latest_record.summary}")

    with right_col:
        st.subheader("重新生成方案")
        st.caption("成功生成后会自动保存 `plan.json` 和 `animation.json`，随后切换到本地播放器视图。")
        with st.form("generate_plan_form", clear_on_submit=False):
            form_col1, form_col2 = st.columns(2)
            with form_col1:
                destination = st.text_input("目的地", value="南京")
                origin = st.text_input("出发地", value="上海")
                departure_date = st.date_input("出发日期", value=date.today() + timedelta(days=1), min_value=date.today())
                days = st.slider("旅行天数", min_value=1, max_value=7, value=3)
                budget = st.number_input("总预算（元）", min_value=300.0, max_value=10000.0, value=1500.0, step=100.0)
                traveler_count = st.number_input("同行人数", min_value=1, max_value=6, value=1, step=1)
                style_label = st.selectbox("旅行节奏", list(STYLE_OPTIONS.keys()), index=1)
                food_budget_label = st.selectbox("餐饮预算风格", list(FOOD_BUDGET_OPTIONS.keys()), index=1)
                hotel_budget_label = st.selectbox("酒店预算风格", list(HOTEL_BUDGET_OPTIONS.keys()), index=1)
            with form_col2:
                interests_labels = st.multiselect("兴趣偏好", list(INTEREST_OPTIONS.keys()), default=["文化", "美食", "自然"])
                taste_preferences = st.multiselect("口味偏好", TASTE_OPTIONS, default=["鲜", "辣"])
                preferred_areas = st.text_input("偏好区域（逗号分隔）", value="")
                hotel_area = st.text_input("酒店偏好区域", value="")
                avoid_tags = st.text_input("想规避的标签（逗号分隔）", value="")
                note_style = st.selectbox("攻略风格", ["小红书风格", "预算友好", "城市漫游"], index=0)
            additional_notes = st.text_area("补充说明", value="希望路线不要太赶，适合答辩演示，兼顾文化、美食和城市代表性景点，并明确酒店与交通安排。", height=90)
            generate = st.form_submit_button("重新生成并保存案例", use_container_width=True)
        if generate:
            request = TripRequest(
                destination=destination.strip(),
                days=days,
                budget=budget,
                origin=origin.strip(),
                departure_date=departure_date.isoformat(),
                traveler_count=int(traveler_count),
                interests=[INTEREST_OPTIONS[item] for item in interests_labels] or ["culture", "food", "nature"],
                preferred_areas=[item.strip() for item in preferred_areas.split(",") if item.strip()],
                avoid_tags=[item.strip() for item in avoid_tags.split(",") if item.strip()],
                food_tastes=taste_preferences,
                style=STYLE_OPTIONS[style_label],
                food_budget_preference=FOOD_BUDGET_OPTIONS[food_budget_label],
                hotel_budget_preference=HOTEL_BUDGET_OPTIONS[hotel_budget_label],
                must_have_hotel_area=hotel_area.strip(),
                travel_note_style=note_style,
                additional_notes=additional_notes.strip(),
            )
            try:
                plan = orchestrator.create_plan(request)
                case_id = build_case_id(request.origin, request.destination, request.days, request.budget)
                animation = _build_animation_bundle(plan, case_id)
                record = save_case(plan, animation, orchestrator.config.tencent_map_js_key)
                _set_loaded_case(plan, animation, record.case_id, load_player_html(record))
                st.session_state["case_refresh_notice"] = f"案例已生成并保存：{record.case_id}。保存位置：outputs/{record.case_id}/"
                st.rerun()
            except Exception as exc:
                st.error(f"在线数据不足，无法生成可靠方案：{exc}")


def _set_loaded_case(plan: TripPlan, animation: AnimationBundle, case_id: str, player_html: str = "") -> None:
    st.session_state["loaded_case_id"] = case_id
    st.session_state["loaded_plan"] = plan
    st.session_state["loaded_animation"] = animation
    st.session_state["loaded_player_html"] = player_html
    st.session_state["latest_request_summary"] = {"origin": plan.request.origin, "destination": plan.request.destination, "days": plan.request.days, "budget": plan.request.budget}
    st.session_state["map_day_filter"] = "全部"
    st.session_state["map_play_mode"] = animation.default_play_mode
    st.session_state["map_frame_index"] = 0
    st.session_state["map_frame_slider"] = 0
    st.session_state["map_is_playing"] = False
    st.session_state["map_view_mode"] = "腾讯JS播放器"
    st.session_state["map_scope_signature"] = ""


def _load_saved_case_with_rebuild(selected_record, orchestrator: TravelPlanningOrchestrator) -> tuple[TripPlan, AnimationBundle, object, str]:
    plan, animation, record = load_case_record(selected_record)
    notice = ""
    if case_requires_rebuild(record, animation):
        rebuilt_animation = _build_animation_bundle(plan, record.case_id)
        record = rebuild_case_assets(record, plan, rebuilt_animation, orchestrator.config.tencent_map_js_key)
        plan, animation, record = load_case_record(record)
        notice = "检测到旧版播放器，已自动重建本地动画资源。"
    return plan, animation, record, notice


def _render_route_map(animation: AnimationBundle, player_html: str, server_key: str) -> None:
    st.radio("地图视图", ["腾讯JS播放器", "Pydeck（备用）", "腾讯静态图（备用）"], horizontal=True, key="map_view_mode")
    if st.session_state["map_view_mode"] == "腾讯JS播放器":
        if player_html:
            components.html(player_html, height=980, scrolling=False)
            st.caption("当前主视图为浏览器端本地动画播放器。播放、切换天数和步骤跳转均在前端完成，不再触发 Streamlit 整页重绘。")
        else:
            st.error("当前案例缺少 player.html，请重新生成一次或重新加载新案例。")
        return

    selected_day_label = st.selectbox(
        "备用视图查看范围",
        ["全部", *sorted({f"第 {node.day} 天" for node in animation.nodes}, key=lambda item: int(item.replace('第 ', '').replace(' 天', '')))],
        key="map_day_filter",
    )
    map_model = _build_map_view_model_from_bundle(animation, selected_day_label)
    if not map_model.nodes:
        st.info("当前没有可展示的路线数据。")
        return

    if st.session_state["map_view_mode"] == "Pydeck（备用）":
        _render_dynamic_map(map_model, map_model.total_frames, False)
    else:
        _render_static_map_backup(map_model, server_key)

    rows = [
        {
            "天数": segment.day,
            "顺序": segment.order,
            "出发节点": segment.from_name,
            "到达节点": segment.to_name,
            "交通方式": _segment_label(segment.segment_type),
            "里程（km）": round(segment.distance_km, 2),
            "预计耗时（分钟）": segment.duration,
            "预计费用（元）": round(segment.cost, 2),
            "说明": segment.desc,
        }
        for segment in map_model.segments
    ]
    if rows:
        st.markdown("**路线说明表**")
        st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _render_dynamic_map(map_model: MapViewModel, current_frame: int, is_animated: bool) -> None:
    deck = _build_pydeck_chart(map_model, current_frame, is_animated)
    st.pydeck_chart(deck, use_container_width=True)
    visible_nodes = map_model.nodes if not is_animated else [node for node in map_model.nodes if node.visible_frame_start <= current_frame]
    visible_segments = map_model.segments if not is_animated else [segment for segment in map_model.segments if segment.visible_frame_start <= current_frame]
    st.caption(f"{'顺序播放' if is_animated else '静态全览'} | 已显示 {len(visible_nodes)} 个点位、{len(visible_segments)} 段路径。")


def _render_current_step_banner(
    current_step: AnimationStep | None,
    current_segment: MapSegment | None,
    selected_day_label: str,
    current_frame: int,
    total_frames: int,
    case_id: str,
) -> None:
    if current_step:
        headline = current_step.headline
        subheadline = current_step.subheadline
        if current_segment is not None:
            subheadline = (
                f"{current_step.subheadline} | 下一段：{_segment_label(current_segment.segment_type)}，"
                f"约 {current_segment.duration} 分钟，{current_segment.cost:.0f} 元"
            )
    else:
        headline = f"{selected_day_label} · 全程总览"
        subheadline = "当前为静态全览或尚未开始播放。"
    st.markdown(
        f"""
        <div style="padding:14px 18px;border-radius:14px;background:linear-gradient(135deg,#eff6ff,#f8fafc);border:1px solid #dbeafe;margin-bottom:14px;">
            <div style="font-size:14px;color:#1d4ed8;font-weight:600;">当前播放</div>
            <div style="font-size:24px;color:#0f172a;font-weight:700;margin-top:4px;">{headline}</div>
            <div style="font-size:14px;color:#475569;margin-top:6px;">{subheadline}</div>
            <div style="font-size:12px;color:#64748b;margin-top:10px;">案例ID：{case_id} | 当前帧：{current_frame}/{total_frames}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_step_panel(steps: list[AnimationStep], segments: list[MapSegment], current_step: AnimationStep | None) -> None:
    st.markdown("**步骤面板**")
    if not steps:
        st.info("当前范围内没有步骤。")
        return
    current_key = (current_step.day, current_step.step_index) if current_step else None
    for step in steps:
        segment = next((item for item in segments if item.day == step.day and item.order == step.step_index), None)
        state = "未开始"
        border = "#cbd5e1"
        background = "#ffffff"
        if current_key and (step.day, step.step_index) == current_key:
            state = "当前步骤"
            border = "#f59e0b"
            background = "#fff7ed"
        elif current_step and step.frame_start < current_step.frame_start:
            state = "已完成"
            border = "#bbf7d0"
            background = "#f0fdf4"
        st.markdown(
            f"""
            <div style="padding:12px 14px;border-radius:14px;border:1px solid {border};background:{background};margin-bottom:10px;">
                <div style="font-size:12px;color:#64748b;">{state}</div>
                <div style="font-size:16px;color:#0f172a;font-weight:700;margin-top:4px;">{step.sidebar_title}</div>
                <div style="font-size:13px;color:#475569;margin-top:6px;">{step.sidebar_desc}</div>
                <div style="font-size:12px;color:#475569;margin-top:6px;">{_sidebar_transport_text(segment)}</div>
                <div style="font-size:12px;color:#64748b;margin-top:8px;">第 {step.day} 天 · 第 {step.step_index} 站</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(f"跳到第 {step.day} 天 第 {step.step_index} 站", key=f"jump_step_{step.day}_{step.step_index}", use_container_width=True):
            st.session_state["map_frame_index"] = step.frame_start
            st.session_state["map_is_playing"] = False
            st.rerun()


def _build_animation_bundle(plan: TripPlan, case_id: str) -> AnimationBundle:
    nodes: list[AnimationNode] = []
    segments: list[AnimationSegment] = []
    steps: list[AnimationStep] = []
    sequence_id = 1

    for day in plan.day_plans:
        timeline = _build_scheduled_day_timeline(day)
        timeline_lookup = {index: item for index, item in enumerate(timeline, start=1)}
        day_nodes: list[AnimationNode] = []
        base_node_index = len(nodes)
        for order, node in enumerate(timeline, start=1):
            frame_start = (sequence_id - 1) * FRAME_STEP
            animation_node = AnimationNode(
                day=day.day,
                step_index=order,
                title=node["name"],
                kind=node["kind"],
                label=node["slot"],
                desc=node["desc"],
                lat=node["lat"],
                lon=node["lon"],
                color=node["color"],
                day_color=DAY_ROUTE_COLORS[(day.day - 1) % len(DAY_ROUTE_COLORS)],
                type_color=POINT_COLORS.get(node["kind"], node["color"]),
                frame_start=frame_start,
                marker_text=_timeline_marker_text(node["kind"], order),
                address=node.get("address", ""),
                district=node.get("district", ""),
            )
            nodes.append(animation_node)
            day_nodes.append(animation_node)
            sequence_id += 1

        for order, (current, nxt) in enumerate(zip(day_nodes, day_nodes[1:]), start=1):
            transport = day.transport_segments[order - 1] if order - 1 < len(day.transport_segments) else None
            route_color = DAY_ROUTE_COLORS[(day.day - 1) % len(DAY_ROUTE_COLORS)]
            raw_path = transport.path if transport and transport.path else []
            segment_path = _normalize_segment_path(raw_path, current.lon, current.lat, nxt.lon, nxt.lat)
            frame_start = current.frame_start
            frame_end = nxt.frame_start
            segments.append(
                AnimationSegment(
                    day=day.day,
                    step_index=order,
                    from_title=current.title,
                    to_title=nxt.title,
                    segment_type=transport.segment_type if transport else "walk",
                    path=segment_path,
                    color=route_color,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    timestamps=_segment_timestamps_raw(segment_path, frame_start, frame_end),
                    duration=transport.duration_minutes if transport else 0,
                    cost=transport.estimated_cost if transport else 0.0,
                    distance_km=transport.distance_km if transport else 0.0,
                    desc=transport.description if transport else f"步行 | {current.title} -> {nxt.title}",
                    arrow_lon=(current.lon + nxt.lon) / 2,
                    arrow_lat=(current.lat + nxt.lat) / 2,
                    angle=_segment_angle(current.lon, current.lat, nxt.lon, nxt.lat),
                    path_status=transport.path_status if transport else "missing",
                )
            )

        for order, node in enumerate(day_nodes, start=1):
            timeline_item = timeline_lookup[order]
            active_segment_indexes = [index for index, segment in enumerate(segments) if segment.day == day.day and segment.step_index in {order, order + 1}]
            next_segment = next((segment for segment in segments if segment.day == day.day and segment.step_index == order), None)
            steps.append(
                AnimationStep(
                    day=day.day,
                    step_index=order,
                    headline=f"第 {day.day} 天 · 第 {order} 站 · {node.title}",
                    subheadline=f"{node.label} | {node.desc} | {timeline_item['start_time']}-{timeline_item['end_time']}",
                    sidebar_title=node.title,
                    sidebar_desc=f"{node.label} · {timeline_item['start_time']}-{timeline_item['end_time']} · 停留 {timeline_item['duration_minutes']} 分钟",
                    node_refs=[base_node_index + order - 1],
                    segment_refs=active_segment_indexes,
                    frame_start=node.frame_start,
                    address=node.address,
                    weather_note=day.weather_summary,
                    next_transport_type=_segment_label(next_segment.segment_type) if next_segment else "",
                    next_transport_duration=next_segment.duration if next_segment else 0,
                    next_transport_cost=next_segment.cost if next_segment else 0.0,
                    next_transport_distance_km=next_segment.distance_km if next_segment else 0.0,
                    next_transport_desc=next_segment.desc if next_segment else "",
                )
            )

    total_frames = max(FRAME_STEP, max((node.frame_start for node in nodes), default=0) + FRAME_STEP)
    return AnimationBundle(
        case_id=case_id,
        request_summary={"origin": plan.request.origin, "destination": plan.request.destination, "days": plan.request.days, "budget": plan.request.budget, "city": plan.city_profile.city},
        nodes=nodes,
        segments=segments,
        steps=steps,
        total_frames=total_frames,
        default_day_mode="全部",
        default_play_mode="静态全览",
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def _build_day_timeline(day) -> list[dict]:
    return scheduling_build_day_timeline(day)


def _build_scheduled_day_timeline(day) -> list[dict]:
    return scheduling_build_scheduled_day_timeline(day)


def _node_duration_minutes(node: dict) -> int:
    return scheduling_node_duration_minutes(node)


def _format_minutes(total_minutes: int) -> str:
    return scheduling_format_minutes(total_minutes)


def _normalize_segment_path(path: list[list[float]], start_lon: float, start_lat: float, end_lon: float, end_lat: float) -> list[list[float]]:
    normalized = [list(point) for point in path if isinstance(point, (list, tuple)) and len(point) >= 2]
    if not normalized:
        return []
    deduped: list[list[float]] = []
    for point in normalized:
        lon = float(point[0])
        lat = float(point[1])
        if _is_valid_lon_lat(lon, lat):
            candidate = [lon, lat]
        elif _is_valid_lon_lat(lat, lon):
            candidate = [lat, lon]
        else:
            continue
        if not deduped or deduped[-1] != candidate:
            deduped.append(candidate)
    if len(deduped) <= 1:
        return []
    # Keep Tencent official route geometry as-is; only snap endpoints when
    # they're already very close to avoid creating artificial cross-water lines.
    start_gap_km = _distance_km(deduped[0][1], deduped[0][0], start_lat, start_lon)
    end_gap_km = _distance_km(deduped[-1][1], deduped[-1][0], end_lat, end_lon)
    if start_gap_km <= 0.35:
        deduped[0] = [float(start_lon), float(start_lat)]
    if end_gap_km <= 0.35:
        deduped[-1] = [float(end_lon), float(end_lat)]
    return deduped


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def _is_valid_lon_lat(lon: float, lat: float) -> bool:
    return math.isfinite(lon) and math.isfinite(lat) and -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def _build_map_view_model(plan: TripPlan, selected_day_label: str) -> MapViewModel:
    return _build_map_view_model_from_bundle(_build_animation_bundle(plan, "preview-case"), selected_day_label)


def _build_map_view_model_from_bundle(animation: AnimationBundle, selected_day_label: str) -> MapViewModel:
    selected_day = None if selected_day_label == "全部" else int(selected_day_label.replace("第 ", "").replace(" 天", ""))
    filtered_nodes = [node for node in animation.nodes if selected_day is None or node.day == selected_day]
    filtered_segments = [segment for segment in animation.segments if selected_day is None or segment.day == selected_day]
    if not filtered_nodes:
        return MapViewModel(nodes=[], segments=[], total_frames=0, center_lat=0.0, center_lon=0.0)

    first_frame = min(node.frame_start for node in filtered_nodes)
    nodes = [
        MapNode(
            day=node.day,
            order=node.step_index,
            name=node.title,
            kind=node.kind,
            slot=node.label,
            desc=node.desc,
            lat=node.lat,
            lon=node.lon,
            color=node.color,
            marker_text=node.marker_text,
            sequence_id=node.step_index,
            visible_frame_start=node.frame_start - first_frame,
        )
        for node in filtered_nodes
    ]
    segments = [
        MapSegment(
            day=segment.day,
            order=segment.step_index,
            from_name=segment.from_title,
            to_name=segment.to_title,
            segment_type=segment.segment_type,
            path=segment.path,
            color=segment.color,
            duration=segment.duration,
            cost=segment.cost,
            distance_km=segment.distance_km,
            desc=segment.desc,
            sequence_id=segment.step_index,
            visible_frame_start=segment.frame_start - first_frame,
            visible_frame_end=segment.frame_end - first_frame,
            arrow_lon=segment.arrow_lon,
            arrow_lat=segment.arrow_lat,
            angle=segment.angle,
            arrow_text=segment.arrow_text,
            path_status=segment.path_status,
        )
        for segment in filtered_segments
    ]
    center_lat = sum(node.lat for node in nodes) / len(nodes)
    center_lon = sum(node.lon for node in nodes) / len(nodes)
    total_frames = max(FRAME_STEP, max(node.visible_frame_start for node in nodes) + FRAME_STEP)
    return MapViewModel(nodes=nodes, segments=segments, total_frames=total_frames, center_lat=center_lat, center_lon=center_lon)


def _filter_animation_steps(animation: AnimationBundle, selected_day_label: str) -> list[AnimationStep]:
    if selected_day_label == "全部":
        return animation.steps
    day = int(selected_day_label.replace("第 ", "").replace(" 天", ""))
    return [step for step in animation.steps if step.day == day]


def _resolve_current_step(steps: list[AnimationStep], current_frame: int, play_mode: str) -> AnimationStep | None:
    if not steps:
        return None
    if play_mode != "顺序播放":
        return steps[0]
    eligible = [step for step in steps if step.frame_start <= current_frame]
    return eligible[-1] if eligible else steps[0]


def _resolve_current_segment(segments: list[MapSegment], current_step: AnimationStep | None) -> MapSegment | None:
    if current_step is None:
        return None
    return next(
        (
            segment
            for segment in segments
            if segment.day == current_step.day and segment.order == current_step.step_index
        ),
        None,
    )


def _sidebar_transport_text(segment: MapSegment | None) -> str:
    if segment is None:
        return "当前站点为当日末站，没有后续市内移动。"
    return f"下一段：{_segment_label(segment.segment_type)} | 约 {segment.duration} 分钟 | {segment.cost:.0f} 元"


def _build_pydeck_chart(map_model: MapViewModel, current_frame: int, is_animated: bool) -> pdk.Deck:
    visible_nodes = map_model.nodes if not is_animated else [node for node in map_model.nodes if node.visible_frame_start <= current_frame]
    visible_segments = map_model.segments if not is_animated else [segment for segment in map_model.segments if segment.visible_frame_start <= current_frame]
    base_node_rows = [_map_node_row(node, active=False) for node in map_model.nodes]
    active_node_rows = [_map_node_row(node, active=True) for node in visible_nodes]
    current_node = max(visible_nodes, key=lambda node: node.visible_frame_start) if visible_nodes else None
    current_node_rows = [_map_node_row(current_node, active=True)] if current_node else []
    segment_rows = [_map_segment_row(segment) for segment in map_model.segments]
    trip_rows = [_trip_segment_row(segment) for segment in visible_segments]
    arrow_rows = [_arrow_row(segment) for segment in visible_segments]

    layers = [
        pdk.Layer("PathLayer", data=segment_rows, get_path="path", get_color="base_color_rgb", get_width=4, width_min_pixels=2, opacity=0.22, pickable=True),
        pdk.Layer("ScatterplotLayer", data=base_node_rows, get_position="[lon, lat]", get_fill_color="color_rgb", get_radius=44, radius_min_pixels=8, opacity=0.18, pickable=True),
        pdk.Layer("ScatterplotLayer", data=active_node_rows, get_position="[lon, lat]", get_fill_color="color_rgb", get_radius=72, radius_min_pixels=12, opacity=0.95, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=2, pickable=True),
        pdk.Layer("TextLayer", data=active_node_rows, get_position="[lon, lat]", get_text="marker_text", get_size=16, get_color=[255, 255, 255], get_alignment_baseline="'center'", get_text_anchor="'middle'"),
    ]
    if current_node_rows:
        layers.append(pdk.Layer("ScatterplotLayer", data=current_node_rows, get_position="[lon, lat]", get_fill_color=[255, 255, 255, 0], get_radius=108, radius_min_pixels=18, stroked=True, get_line_color=[250, 204, 21], line_width_min_pixels=4))
    if is_animated and trip_rows:
        layers.append(pdk.Layer("TripsLayer", data=trip_rows, get_path="path", get_timestamps="timestamps", get_color="color_rgb", opacity=0.98, width_min_pixels=8, rounded=True, trail_length=FRAME_STEP * 1.8, current_time=current_frame))
    elif visible_segments:
        layers.append(pdk.Layer("PathLayer", data=[_map_segment_row(segment) for segment in visible_segments], get_path="path", get_color="color_rgb", get_width=7, width_min_pixels=4, opacity=0.92))
    if arrow_rows:
        layers.append(pdk.Layer("TextLayer", data=arrow_rows, get_position="[arrow_lon, arrow_lat]", get_text="arrow_text", get_size=18, get_color=[51, 65, 85], get_angle="angle", get_alignment_baseline="'center'", get_text_anchor="'middle'"))
    return pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(latitude=map_model.center_lat, longitude=map_model.center_lon, zoom=_estimate_zoom(map_model.nodes), pitch=42, bearing=0),
        map_provider="carto",
        map_style="road",
        tooltip={"html": "<b>{title}</b><br/>{subtitle}<br/>{detail}", "style": {"backgroundColor": "#0f172a", "color": "white", "fontSize": "13px"}},
    )


def _render_static_map_backup(map_model: MapViewModel, server_key: str) -> None:
    if not server_key:
        st.error("缺少 TENCENT_MAP_SERVER_KEY，无法显示腾讯静态地图备用视图。")
        return
    point_rows = [{"lat": node.lat, "lon": node.lon, "kind": node.kind, "day": node.day, "name": node.name, "slot": node.slot, "desc": node.desc, "color": node.color} for node in map_model.nodes]
    segment_rows = [{"path": segment.path, "color": segment.color} for segment in map_model.segments]
    static_map_url = _build_tencent_static_map_url(point_rows, segment_rows, server_key)
    st.image(static_map_url, caption=f"腾讯静态备用图：共 {len(point_rows)} 个点位，{len(segment_rows)} 段路线。")
    st.caption("当前为备用静态图视图，适合答辩时快速兜底。")


def _evidence_row(day: int, object_type: str, name: str, evidence) -> dict:
    return {
        "分组": f"第 {day} 天",
        "天数": day,
        "对象类型": object_type,
        "名称": name,
        "证据类型": evidence.evidence_type,
        "来源别名": evidence.provider_label or evidence.provider,
        "来源提供方": evidence.provider,
        "标题": evidence.title,
        "摘要": evidence.snippet,
        "链接": evidence.source_url,
    }


def _provider_label(evidence) -> str:
    return evidence.provider_label or evidence.provider


def _segment_label(segment_type: str) -> str:
    return {"intercity": "高铁 / 飞机 / 城际", "taxi": "打车", "metro": "地铁", "bus": "公交", "walk": "步行"}.get(segment_type, segment_type)


def _build_tencent_static_map_url(point_rows: list[dict], segment_rows: list[dict], server_key: str) -> str:
    center_lon = sum(item["lon"] for item in point_rows) / len(point_rows)
    center_lat = sum(item["lat"] for item in point_rows) / len(point_rows)
    params = [("size", "640*420"), ("scale", "2"), ("zoom", "12"), ("center", f"{center_lat:.6f},{center_lon:.6f}"), ("key", server_key)]
    for idx, point in enumerate(point_rows, start=1):
        params.append(("markers", f"size:large|color:{_point_marker_color(point['kind'])}|label:{_point_marker_label(point, idx)}|{point['lat']:.6f},{point['lon']:.6f}"))
    for segment in segment_rows:
        sampled = _sample_path_points(segment["path"], max_points=10)
        if len(sampled) < 2:
            continue
        path_points = ";".join(f"{lat:.6f},{lon:.6f}" for lon, lat in sampled)
        params.append(("path", f"color:{_segment_path_color(segment['color'])}|weight:5|{path_points}"))
    query = "&".join(f"{quote(key)}={quote(value, safe=':;,*|#.')}" for key, value in params)
    return f"https://apis.map.qq.com/ws/staticmap/v2/?{query}"


def _point_marker_label(point: dict, idx: int) -> str:
    if point["kind"] == "hotel":
        return "住"
    if point["kind"] == "lunch":
        return "午"
    if point["kind"] == "dinner":
        return "晚"
    sequence = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return sequence[(idx - 1) % len(sequence)]


def _point_marker_color(kind: str) -> str:
    return {"hotel": "orange", "spot": "blue", "lunch": "green", "dinner": "red"}.get(kind, "gray")


def _segment_path_color(color: str) -> str:
    return f"0x{color.lstrip('#').upper()}"


def _sample_path_points(path: list[list[float]], max_points: int = 10) -> list[list[float]]:
    if len(path) <= max_points:
        return path
    step = max(1, len(path) // (max_points - 1))
    sampled = path[::step]
    if sampled[-1] != path[-1]:
        sampled.append(path[-1])
    return sampled[:max_points]


def _map_node_row(node: MapNode, active: bool) -> dict:
    color_rgb = _hex_to_rgb(node.color)
    if not active:
        color_rgb = color_rgb + [80]
    return {"lon": node.lon, "lat": node.lat, "color_rgb": color_rgb, "marker_text": node.marker_text, "title": f"第 {node.day} 天 · {node.slot}", "subtitle": node.name, "detail": node.desc}


def _map_segment_row(segment: MapSegment) -> dict:
    return {"path": segment.path, "color_rgb": _hex_to_rgb(segment.color), "base_color_rgb": _hex_to_rgb(segment.color) + [80], "title": f"第 {segment.day} 天 · {_segment_label(segment.segment_type)}", "subtitle": f"{segment.from_name} -> {segment.to_name}", "detail": f"{segment.distance_km:.1f} km | {segment.duration} 分钟 | {segment.cost:.0f} 元"}


def _trip_segment_row(segment: MapSegment) -> dict:
    return {"path": segment.path, "timestamps": _segment_timestamps(segment), "color_rgb": _hex_to_rgb(segment.color), "title": f"第 {segment.day} 天 · {_segment_label(segment.segment_type)}", "subtitle": f"{segment.from_name} -> {segment.to_name}", "detail": f"{segment.distance_km:.1f} km | {segment.duration} 分钟 | {segment.cost:.0f} 元"}


def _arrow_row(segment: MapSegment) -> dict:
    return {"arrow_lon": segment.arrow_lon, "arrow_lat": segment.arrow_lat, "arrow_text": segment.arrow_text, "angle": segment.angle}


def _timeline_marker_text(kind: str, order: int) -> str:
    if kind == "hotel":
        return "住"
    if kind == "lunch":
        return "午"
    if kind == "dinner":
        return "晚"
    sequence = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return sequence[(order - 1) % len(sequence)]


def _segment_angle(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    return math.degrees(math.atan2(lat2 - lat1, lon2 - lon1))


def _estimate_zoom(nodes: list[MapNode]) -> float:
    if len(nodes) <= 1:
        return 13
    lat_span = max(node.lat for node in nodes) - min(node.lat for node in nodes)
    lon_span = max(node.lon for node in nodes) - min(node.lon for node in nodes)
    span = max(lat_span, lon_span)
    if span < 0.02:
        return 13
    if span < 0.05:
        return 12
    if span < 0.12:
        return 11
    if span < 0.3:
        return 10
    return 9


def _hex_to_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[index : index + 2], 16) for index in (0, 2, 4)]


def _densify_path(path: list[list[float]], steps: int = 12) -> list[list[float]]:
    if len(path) < 2:
        return path
    start_lon, start_lat = path[0]
    end_lon, end_lat = path[-1]
    dense: list[list[float]] = []
    for idx in range(steps + 1):
        t = idx / steps
        dense.append([start_lon + (end_lon - start_lon) * t, start_lat + (end_lat - start_lat) * t])
    return dense


def _segment_timestamps(segment: MapSegment) -> list[float]:
    return _segment_timestamps_raw(segment.path, segment.visible_frame_start, segment.visible_frame_end)


def _segment_timestamps_raw(path: list[list[float]], frame_start: int, frame_end: int) -> list[float]:
    if len(path) <= 1:
        return [float(frame_start)]
    span = max(1, frame_end - frame_start)
    step = span / max(1, len(path) - 1)
    return [frame_start + idx * step for idx in range(len(path))]
