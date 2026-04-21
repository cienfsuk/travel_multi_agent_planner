import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Calendar, FileDown, ShieldCheck, Target, X } from "lucide-react";
import type {
  AgentTraceStep,
  AnimationStep,
  SourceEvidence,
  PlanResponse,
  TransportSegment,
} from "../types/api";
import {
  translateNoteStyle,
  translateEvidenceType,
  translateTags,
} from "../utils/translations";
import TripMapView, { type TripMapHandle } from "./TripMapView";

interface Props {
  result: PlanResponse;
}

type ResultSection = "overview" | "itinerary" | "insights" | "export";

type EvidenceRow = {
  day: number | null;
  objectType: "景点" | "餐饮" | "酒店";
  name: string;
  evidenceType: string;
  provider: string;
  title: string;
  snippet: string;
  link?: string;
};

type UnifiedRiskItem = {
  severity: string;
  category?: string;
  day?: number | null;
  message: string;
  suggestion?: string;
};

function summarizeSegment(segment?: TransportSegment | null): string {
  if (!segment) {
    return "暂无";
  }
  const mode =
    {
      intercity: "高铁/飞机/城际",
      taxi: "打车",
      metro: "地铁",
      bus: "公交",
      walk: "步行",
    }[segment.segment_type] || segment.segment_type;
  return `${mode} · ${segment.from_label} → ${segment.to_label} · ${segment.duration_minutes} 分钟 · ¥${segment.estimated_cost.toFixed(0)}`;
}

function summarizeStepTransport(step: AnimationStep) {
  if (!step.next_transport_type) {
    return null;
  }

  const detailParts = [
    `约 ${step.next_transport_duration ?? 0} 分钟`,
    `¥${(step.next_transport_cost ?? 0).toFixed(0)}`,
  ];

  if (typeof step.next_transport_distance_km === "number") {
    detailParts.push(`${step.next_transport_distance_km.toFixed(1)} km`);
  }

  return `${step.next_transport_type} · ${detailParts.join(" · ")}`;
}

function flattenEvidence(plan: PlanResponse["plan"]): EvidenceRow[] {
  const rows: EvidenceRow[] = [];
  const pushEvidence = (
    day: number,
    objectType: EvidenceRow["objectType"],
    name: string,
    evidences: SourceEvidence[] | undefined,
  ) => {
    for (const evidence of evidences || []) {
      rows.push({
        day,
        objectType,
        name,
        evidenceType: evidence.evidence_type || "未标注",
        provider: evidence.provider_label || evidence.provider || "未知",
        title: evidence.title || name,
        snippet: evidence.snippet || "",
        link: evidence.source_url,
      });
    }
  };

  for (const day of plan.days || []) {
    for (const spot of day.spots || []) {
      pushEvidence(day.day, "景点", spot.name, spot.source_evidence);
    }
    for (const meal of day.meals || []) {
      pushEvidence(day.day, "餐饮", meal.venue_name, meal.source_evidence);
    }
    if (day.hotel) {
      pushEvidence(day.day, "酒店", day.hotel.name, day.hotel.source_evidence);
    }
  }
  return rows;
}

const MAP_DAY_COLORS = [
  "#2563eb",
  "#f97316",
  "#0f766e",
  "#7c3aed",
  "#dc2626",
  "#0891b2",
  "#84cc16",
];

function downloadText(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function ResultView({ result }: Props) {
  const sections: Array<{ key: ResultSection; label: string }> = [
    { key: "overview", label: "总览" },
    { key: "itinerary", label: "行程" },
    { key: "insights", label: "洞察" },
    { key: "export", label: "导出" },
  ];
  const [showRiskModal, setShowRiskModal] = useState(false);
  const [activeSection, setActiveSection] = useState<ResultSection>("overview");
  const [activeDay, setActiveDay] = useState<number>(
    result.plan.days[0]?.day || 1,
  );
  const [evidencePage, setEvidencePage] = useState(1);
  const [mapDay, setMapDay] = useState<number | null>(null);
  const [mapStepIndex, setMapStepIndex] = useState(0);
  const [isMapPlaying, setIsMapPlaying] = useState(false);
  const [mapPlaySpeed, setMapPlaySpeed] = useState<"slow" | "normal" | "fast">(
    "normal",
  );
  const mapRef = useRef<TripMapHandle>(null);

  const { plan, animation } = result;

  const filteredAnimSteps = useMemo(() => {
    if (!animation) return [];
    return mapDay != null
      ? animation.steps.filter((s) => s.day === mapDay)
      : animation.steps;
  }, [animation, mapDay]);

  const currentAnimStep = filteredAnimSteps[mapStepIndex] || null;

  // Prevent background content from scrolling/bleeding through when risk modal is open.
  useEffect(() => {
    if (!showRiskModal) return;
    const prevOverflow = document.body.style.overflow;
    const prevOverscrollBehavior = document.body.style.overscrollBehavior;
    document.body.style.overflow = "hidden";
    document.body.style.overscrollBehavior = "none";
    return () => {
      document.body.style.overflow = prevOverflow;
      document.body.style.overscrollBehavior = prevOverscrollBehavior;
    };
  }, [showRiskModal]);

  // Reset step index when day filter changes
  useEffect(() => {
    setMapStepIndex(0);
    setIsMapPlaying(false);
  }, [mapDay]);

  // Playback timer
  useEffect(() => {
    if (!isMapPlaying || !filteredAnimSteps.length) return;
    const speedMs = { slow: 2200, normal: 1400, fast: 860 }[mapPlaySpeed];
    const timer = setInterval(() => {
      setMapStepIndex((i) => {
        if (i >= filteredAnimSteps.length - 1) {
          setIsMapPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, speedMs);
    return () => clearInterval(timer);
  }, [isMapPlaying, mapPlaySpeed, filteredAnimSteps.length]);

  // Fly to step node when step changes
  useEffect(() => {
    if (!animation || !mapRef.current) return;
    const steps =
      mapDay != null
        ? animation.steps.filter((s) => s.day === mapDay)
        : animation.steps;
    const step = steps[mapStepIndex];
    if (!step) return;
    const node = animation.nodes.find(
      (n) => n.day === step.day && n.step_index === step.step_index,
    );
    if (node) {
      mapRef.current.flyTo(node.lat, node.lon, node.day, node.step_index);
    }
  }, [mapStepIndex, mapDay, animation]);

  const handleMapPrevStep = () => {
    setMapStepIndex((i) => Math.max(0, i - 1));
    setIsMapPlaying(false);
  };
  const handleMapNextStep = () => {
    setMapStepIndex((i) => Math.min(filteredAnimSteps.length - 1, i + 1));
    setIsMapPlaying(false);
  };
  const handleMapPlayPause = () => setIsMapPlaying((v) => !v);
  const handleMapReset = () => {
    setMapStepIndex(0);
    setIsMapPlaying(false);
  };
  const handleJumpToStep = (idx: number) => {
    setMapStepIndex(idx);
    setIsMapPlaying(false);
  };

  useEffect(() => {
    if (!plan.days.length) {
      return;
    }
    const exists = plan.days.some((day) => day.day === activeDay);
    if (!exists) {
      setActiveDay(plan.days[0].day);
    }
  }, [activeDay, plan.days]);

  const evidenceRows = useMemo(() => flattenEvidence(plan), [plan]);
  const riskItems = useMemo<UnifiedRiskItem[]>(() => {
    const validationIssues = plan.validation_issues ?? [];
    const issueMessages = new Set(
      validationIssues.map((issue) => issue.message?.trim()).filter(Boolean),
    );

    return [
      ...validationIssues.map((issue) => ({
        severity: issue.severity?.toLowerCase() ?? "info",
        category: issue.category,
        day: issue.day,
        message: issue.message,
        suggestion: issue.suggested_fix,
      })),
      ...plan.warnings
        .filter((warning) => !issueMessages.has(warning.trim()))
        .map((warning) => ({
          severity: "warning",
          message: warning,
        })),
    ];
  }, [plan.validation_issues, plan.warnings]);
  const riskCount = riskItems.length;

  useEffect(() => {
    setEvidencePage(1);
  }, [evidenceRows.length]);

  const evidencePageSize = 20;
  const evidenceTotalPages = Math.max(
    1,
    Math.ceil(evidenceRows.length / evidencePageSize),
  );
  const pagedEvidence = useMemo(() => {
    const start = (evidencePage - 1) * evidencePageSize;
    return evidenceRows.slice(start, start + evidencePageSize);
  }, [evidencePage, evidenceRows]);

  const traceRows = plan.trace || [];
  return (
    <div className="relative flex h-[calc(100svh-64px)] flex-col overflow-hidden py-2">
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="mb-3 shrink-0 flex flex-wrap items-center justify-between gap-3">
          <div className="w-full rounded-2xl border border-indigo-100 bg-gradient-to-r from-white via-indigo-50/70 to-sky-50/70 p-4 shadow-sm dark:border-indigo-900/50 dark:from-slate-900 dark:via-slate-900 dark:to-indigo-950/30">
            <h2 className="text-2xl font-extrabold tracking-tight text-slate-900 dark:text-white">
              <span className="bg-gradient-to-r from-indigo-600 via-sky-500 to-cyan-500 bg-clip-text text-transparent">
                {plan.request.destination}
              </span>{" "}
              行程结果
            </h2>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs sm:text-sm">
              <span className="rounded-full border border-slate-200 bg-white px-3 py-1 font-medium text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200">
                {plan.request.origin} → {plan.request.destination}
              </span>
              <span className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 font-medium text-indigo-700 dark:border-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300">
                {plan.days.length} 天
              </span>
              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 font-medium text-emerald-700 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
                预算 ¥{plan.request.budget.toFixed(0)}
              </span>
            </div>
          </div>
        </div>

        <div className="mb-3 shrink-0 flex w-full items-center gap-2 overflow-hidden rounded-xl border border-slate-200 bg-white p-1 dark:border-slate-700 dark:bg-slate-800">
          {sections.map((section) => (
            <button
              key={section.key}
              onClick={() => setActiveSection(section.key)}
              className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition ${
                activeSection === section.key
                  ? "bg-indigo-600 text-white shadow"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700"
              }`}
            >
              {section.label}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 flex flex-col overflow-hidden pr-1 pb-3">
          {activeSection === "overview" && (
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pb-6 pr-1">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                  <div className="text-xs text-slate-500">预计花费</div>
                  <div className="mt-1 text-2xl font-bold text-slate-900 dark:text-slate-100">
                    ¥{plan.budget_summary.total_estimated.toFixed(0)}
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                  <div className="text-xs text-slate-500">预算结余</div>
                  <div
                    className={`mt-1 text-2xl font-bold ${
                      plan.budget_summary.remaining_budget >= 0
                        ? "text-green-600"
                        : "text-red-600"
                    }`}
                  >
                    ¥{plan.budget_summary.remaining_budget.toFixed(0)}
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                  <div className="text-xs text-slate-500">校验评分</div>
                  <div className="mt-1 flex items-center gap-2 text-2xl font-bold text-slate-900 dark:text-slate-100">
                    <ShieldCheck className="h-5 w-5 text-indigo-500" />
                    {plan.final_score.toFixed(0)}
                  </div>
                </div>
                <button
                  onClick={() => setShowRiskModal(true)}
                  className="rounded-2xl border border-slate-200 bg-white p-4 text-left transition hover:border-amber-300 hover:shadow-md dark:border-slate-700 dark:bg-slate-800 dark:hover:border-amber-600"
                >
                  <div className="text-xs text-slate-500">风险提示</div>
                  <div className="mt-1 text-2xl font-bold text-amber-600">
                    {riskCount}
                  </div>
                  <div className="mt-1 text-xs text-amber-500">
                    {riskCount > 0 ? "点击查看详情" : "无风险"}
                  </div>
                </button>
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800 lg:col-span-2">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                    <Target className="h-4 w-4 text-indigo-500" />{" "}
                    城市确认与模式状态
                  </div>
                  <div className="space-y-2 text-sm text-slate-600 dark:text-slate-300">
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      <span>
                        <span className="text-slate-400">运行模式：</span>
                        {plan.evidence_mode_summary ||
                          "在线优先 + 预生成动画包"}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      <span>
                        <span className="text-slate-400">城市确认：</span>
                        {plan.origin_match?.confirmed_name ||
                          plan.request.origin}
                        {" → "}
                        {plan.destination_match?.confirmed_name ||
                          plan.request.destination}
                      </span>
                      {plan.request.departure_date && (
                        <span>
                          <span className="text-slate-400">出发时间：</span>
                          {plan.request.departure_date}
                        </span>
                      )}
                      {plan.request.traveler_count && (
                        <span>
                          <span className="text-slate-400">出行人数：</span>
                          {plan.request.traveler_count} 人
                        </span>
                      )}
                    </div>
                    {!!plan.search_notes?.length && (
                      <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-900/40">
                        {plan.search_notes.slice(0, 3).map((note, idx) => (
                          <div key={idx}>- {note}</div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                      <Calendar className="h-4 w-4 text-indigo-500" />{" "}
                      来往交通摘要
                    </div>
                    {(() => {
                      const arrivalCost =
                        plan.days[0]?.arrival_segment?.estimated_cost ?? 0;
                      const departureCost =
                        plan.days[plan.days.length - 1]?.departure_segment
                          ?.estimated_cost ?? 0;
                      const total = arrivalCost + departureCost;
                      return total > 0 ? (
                        <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300">
                          合计 ¥{total.toFixed(0)}
                        </span>
                      ) : null;
                    })()}
                  </div>
                  <div className="space-y-3">
                    {[
                      {
                        label: "去程",
                        segment: plan.days[0]?.arrival_segment,
                        color: "bg-green-500",
                      },
                      {
                        label: "返程",
                        segment:
                          plan.days[plan.days.length - 1]?.departure_segment,
                        color: "bg-rose-500",
                      },
                    ].map(({ label, segment, color }) => (
                      <div
                        key={label}
                        className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-900/40"
                      >
                        <div className="mb-1 flex items-center gap-1.5">
                          <span className={`h-2 w-2 rounded-full ${color}`} />
                          <span className="text-xs font-semibold text-slate-700 dark:text-slate-200">
                            {label}
                          </span>
                          {segment?.estimated_cost ? (
                            <span className="ml-auto text-xs font-medium text-indigo-600 dark:text-indigo-400">
                              ¥{segment.estimated_cost.toFixed(0)}
                            </span>
                          ) : null}
                        </div>
                        <div className="text-xs text-slate-600 dark:text-slate-300">
                          {summarizeSegment(segment)}
                        </div>
                        {(segment?.source_name || segment?.source_url) && (
                          <div className="mt-1 flex items-center gap-1 text-xs text-slate-400 dark:text-slate-500">
                            <span>来源：</span>
                            {segment.source_url ? (
                              <a
                                href={segment.source_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="truncate text-indigo-500 underline-offset-2 hover:underline"
                              >
                                {segment.source_name || segment.source_url}
                              </a>
                            ) : (
                              <span>{segment.source_name}</span>
                            )}
                            {segment.queried_at && (
                              <span className="ml-1">
                                · {segment.queried_at.slice(0, 10)}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                <div className="mb-4 text-sm font-semibold text-slate-700 dark:text-slate-200">
                  预算分解
                </div>
                {(() => {
                  const lines = plan.budget_summary.lines;
                  const maxAmount = Math.max(...lines.map((l) => l.amount), 1);
                  const palette = [
                    "bg-indigo-500",
                    "bg-violet-500",
                    "bg-blue-500",
                    "bg-teal-500",
                    "bg-green-500",
                    "bg-amber-500",
                    "bg-orange-500",
                    "bg-rose-500",
                  ];
                  return (
                    <div className="space-y-3">
                      {lines.map((line, idx) => {
                        const pct = (line.amount / maxAmount) * 100;
                        const bar = palette[idx % palette.length];
                        return (
                          <div key={idx}>
                            <div className="mb-1 flex items-baseline justify-between text-sm">
                              <span className="font-medium text-slate-800 dark:text-slate-100">
                                {line.category}
                              </span>
                              <span className="text-slate-600 dark:text-slate-300">
                                ¥{line.amount.toFixed(0)}
                              </span>
                            </div>
                            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                              <div
                                className={`h-full rounded-full ${bar} transition-all duration-500`}
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                            {line.note && (
                              <div className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
                                {line.note}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  );
                })()}
              </div>
            </div>
          )}

          {activeSection === "itinerary" && (
            <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pb-6">
              {/* Controls bar */}
              <div className="shrink-0 flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-800">
                <div className="flex flex-wrap gap-1.5">
                  <button
                    onClick={() => setMapDay(null)}
                    className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                      mapDay === null
                        ? "bg-indigo-600 text-white shadow"
                        : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-200"
                    }`}
                  >
                    全部
                  </button>
                  {plan.days.map((d) => (
                    <button
                      key={d.day}
                      onClick={() => setMapDay(d.day)}
                      className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                        mapDay === d.day
                          ? "bg-indigo-600 text-white shadow"
                          : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-200"
                      }`}
                    >
                      第 {d.day} 天
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={handleMapPrevStep}
                    disabled={mapStepIndex <= 0}
                    className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-40 dark:border-slate-600 dark:text-slate-200"
                  >
                    ◀ 上一步
                  </button>
                  <button
                    onClick={handleMapPlayPause}
                    className={`rounded-lg px-3 py-1.5 text-xs font-bold transition ${
                      isMapPlaying
                        ? "bg-amber-500 text-white hover:bg-amber-600"
                        : "bg-indigo-600 text-white hover:bg-indigo-700"
                    }`}
                  >
                    {isMapPlaying ? "⏸ 暂停" : "▶ 播放"}
                  </button>
                  <button
                    onClick={handleMapNextStep}
                    disabled={mapStepIndex >= filteredAnimSteps.length - 1}
                    className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-40 dark:border-slate-600 dark:text-slate-200"
                  >
                    下一步 ▶
                  </button>
                  <button
                    onClick={handleMapReset}
                    className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200"
                  >
                    重置
                  </button>
                  <select
                    value={mapPlaySpeed}
                    onChange={(e) =>
                      setMapPlaySpeed(
                        e.target.value as "slow" | "normal" | "fast",
                      )
                    }
                    className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
                  >
                    <option value="slow">慢速</option>
                    <option value="normal">标准</option>
                    <option value="fast">快速</option>
                  </select>
                  <span className="text-xs text-slate-500">
                    {filteredAnimSteps.length > 0
                      ? `${mapStepIndex + 1} / ${filteredAnimSteps.length}`
                      : "0 / 0"}
                  </span>
                </div>
              </div>

              {/* Main content: map + side panel */}
              <div className="flex flex-col gap-4 lg:min-h-0 lg:flex-1 lg:flex-row">
                {/* Map */}
                <div className="relative h-[40vh] min-h-[300px] w-full overflow-y-auto rounded-2xl border border-slate-200 dark:border-slate-700 lg:h-full lg:w-[62%]">
                  <TripMapView
                    ref={mapRef}
                    animation={animation}
                    selectedDay={mapDay}
                    activeStepKey={
                      currentAnimStep
                        ? {
                            day: currentAnimStep.day,
                            stepIndex: currentAnimStep.step_index,
                          }
                        : null
                    }
                  />
                  {/* HUD overlay */}
                  {currentAnimStep && (
                    <div className="pointer-events-none absolute top-4 left-4 z-10 max-w-[420px] rounded-2xl border border-white/10 bg-slate-900/75 px-4 py-3 text-white backdrop-blur-md">
                      <div className="mb-1 text-xs text-slate-300">
                        当前站点
                      </div>
                      <div className="text-lg font-bold leading-tight">
                        {currentAnimStep.headline}
                      </div>
                      <div className="mt-1 text-xs text-slate-300">
                        {currentAnimStep.subheadline}
                      </div>
                      <div className="mt-2 text-xs text-slate-400">
                        {mapDay === null ? "全部" : `第 ${mapDay} 天`} ·{" "}
                        {mapStepIndex + 1}/{filteredAnimSteps.length} ·{" "}
                        {isMapPlaying ? "播放中" : "已暂停"}
                      </div>
                    </div>
                  )}
                </div>

                {/* Side panel */}
                <div className="flex w-full flex-col rounded-2xl border border-slate-200 bg-white px-4 py-4 dark:border-slate-700 dark:bg-slate-800/50 lg:h-full lg:w-[38%] lg:overflow-y-auto">
                  <div className="max-h-[50vh] overflow-y-auto lg:min-h-0 lg:max-h-none lg:flex-1">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        步骤面板
                      </span>
                      <span className="text-xs text-slate-400">
                        {filteredAnimSteps.length} 站
                      </span>
                    </div>
                    <div className="space-y-2">
                      {filteredAnimSteps.map((step, idx) => (
                        <div
                          key={`${step.day}-${step.step_index}`}
                          onClick={() => handleJumpToStep(idx)}
                          className={`cursor-pointer rounded-xl border p-3 transition hover:shadow-md ${
                            idx === mapStepIndex
                              ? "border-amber-400 bg-amber-50 shadow dark:border-amber-600 dark:bg-amber-950/30"
                              : idx < mapStepIndex
                                ? "border-green-200 bg-green-50/50 dark:border-green-800/50 dark:bg-green-950/20"
                                : "border-slate-200 bg-white hover:border-indigo-200 dark:border-slate-700 dark:bg-slate-800"
                          }`}
                        >
                          <div className="flex items-center justify-between text-xs text-slate-500">
                            <span
                              className="font-semibold"
                              style={{
                                color:
                                  MAP_DAY_COLORS[
                                    (step.day - 1) % MAP_DAY_COLORS.length
                                  ],
                              }}
                            >
                              第 {step.day} 天 · 第 {step.step_index} 站
                            </span>
                            <span>
                              {idx === mapStepIndex
                                ? "当前"
                                : idx < mapStepIndex
                                  ? "已完成"
                                  : "未开始"}
                            </span>
                          </div>
                          <div className="mt-1 text-sm font-bold text-slate-800 dark:text-slate-100">
                            {step.sidebar_title}
                          </div>
                          <div className="mt-0.5 text-xs text-slate-500">
                            {step.sidebar_desc}
                          </div>
                          {step.address && (
                            <div className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                              {step.address}
                            </div>
                          )}
                          {step.weather_note && (
                            <div className="mt-1 text-[11px] text-teal-600 dark:text-teal-400">
                              天气：{step.weather_note}
                            </div>
                          )}
                          <div className="mt-2 rounded-lg border border-slate-100 bg-slate-50/80 px-2.5 py-2 dark:border-slate-700 dark:bg-slate-900/40">
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              下一段交通
                            </div>
                            {summarizeStepTransport(step) ? (
                              <>
                                <div className="mt-1 text-xs font-medium text-slate-700 dark:text-slate-200">
                                  {summarizeStepTransport(step)}
                                </div>
                                {step.next_transport_desc && (
                                  <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                                    {step.next_transport_desc}
                                  </div>
                                )}
                              </>
                            ) : (
                              <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                                当前站点为当日末站，没有后续市内移动。
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeSection === "insights" && (
            <div className="min-h-0 flex-1 grid grid-cols-1 gap-4 overflow-y-auto pb-6 xl:grid-cols-3">
              <section className="flex h-[72vh] flex-col rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
                  攻略摘要
                </h3>
                <div className="flex-1 space-y-3 overflow-y-auto pr-1">
                  {(plan.travel_notes || []).length === 0 && (
                    <div className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-500 dark:border-slate-600 dark:text-slate-400">
                      当前案例没有额外攻略摘要。
                    </div>
                  )}
                  {(plan.travel_notes || []).map((note, idx) => (
                    <div
                      key={idx}
                      className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"
                    >
                      <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                        {note.title}
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        {translateNoteStyle(note.style_tag) || "未标注风格"} ·{" "}
                        {translateEvidenceType(note.evidence_type) || "未标注证据类型"}
                      </div>
                      <div className="mt-2 text-sm text-slate-600 dark:text-slate-300">
                        {note.summary}
                      </div>
                      {note.source_url && (
                        <a
                          href={note.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-block text-xs text-indigo-600 hover:underline"
                        >
                          查看来源
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              </section>

              <section className="flex h-[72vh] flex-col rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
                  证据来源
                </h3>
                <div className="mb-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  共 {evidenceRows.length} 条证据 · 第 {evidencePage}/
                  {evidenceTotalPages} 页
                </div>

                <div className="flex-1 space-y-2 overflow-y-auto pr-1">
                  {pagedEvidence.map((row, idx) => (
                    <div
                      key={idx}
                      className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700"
                    >
                      <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
                        <span>
                          {row.day ? `第 ${row.day} 天` : "-"} ·{" "}
                          {row.objectType}
                        </span>
                        <span>{row.provider}</span>
                      </div>
                      <div className="mt-1 font-medium text-slate-900 dark:text-slate-100">
                        {row.name}
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        {translateEvidenceType(row.evidenceType)} · {row.title}
                      </div>
                      {!!row.snippet && (
                        <div className="mt-2 text-xs text-slate-600 dark:text-slate-300">
                          {row.snippet}
                        </div>
                      )}
                      {row.link && (
                        <a
                          href={row.link}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-block text-xs text-indigo-600 hover:underline"
                        >
                          打开来源链接
                        </a>
                      )}
                    </div>
                  ))}
                  {evidenceRows.length === 0 && (
                    <div className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-500 dark:border-slate-600 dark:text-slate-400">
                      当前案例没有证据记录。
                    </div>
                  )}
                </div>
                {evidenceRows.length > 0 && (
                  <div className="mt-3 flex items-center justify-between">
                    <button
                      onClick={() =>
                        setEvidencePage((page) => Math.max(1, page - 1))
                      }
                      disabled={evidencePage <= 1}
                      className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
                    >
                      上一页
                    </button>
                    <span className="text-xs text-slate-500">
                      {evidencePage}/{evidenceTotalPages}
                    </span>
                    <button
                      onClick={() =>
                        setEvidencePage((page) =>
                          Math.min(evidenceTotalPages, page + 1),
                        )
                      }
                      disabled={evidencePage >= evidenceTotalPages}
                      className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
                    >
                      下一页
                    </button>
                  </div>
                )}
              </section>

              <section className="flex h-[72vh] flex-col rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
                <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Agent 轨迹
                </h3>
                <div className="flex-1 space-y-2 overflow-y-auto pr-1">
                  {(plan.trace || []).length === 0 && (
                    <div className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-500 dark:border-slate-600 dark:text-slate-400">
                      当前案例没有 Agent 轨迹。
                    </div>
                  )}
                  {traceRows.map((step: AgentTraceStep, idx: number) => (
                    <div
                      key={idx}
                      className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                          {step.agent_name}
                        </div>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {step.status || "ok"}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        输入：{step.input_summary}
                      </div>
                      <div className="mt-1 text-sm text-slate-700 dark:text-slate-300">
                        输出：{step.output_summary}
                      </div>
                      {!!step.key_decisions?.length && (
                        <div className="mt-2 text-xs text-slate-500">
                          决策：{step.key_decisions.slice(0, 3).join(" | ")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            </div>
          )}

          {activeSection === "export" && (
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-6 pr-2 shadow-sm dark:border-slate-700 dark:bg-slate-800">
              <div>
                <div className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                  导出中心
                </div>
                <div className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                  一键下载结构化数据与旅行手册，下方可直接查看 Markdown。
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-900/40">
                  <button
                    onClick={() =>
                      downloadText(
                        `${plan.request.destination.toLowerCase()}_trip_plan.json`,
                        JSON.stringify(plan, null, 2),
                        "application/json",
                      )
                    }
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-indigo-200 bg-white px-4 py-3 text-sm font-medium text-indigo-700 transition hover:border-indigo-400 hover:bg-indigo-50 dark:border-indigo-700 dark:bg-slate-900 dark:text-indigo-300"
                  >
                    <FileDown className="h-4 w-4" /> 下载计划 JSON
                  </button>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-900/40">
                  <button
                    onClick={() =>
                      downloadText(
                        `${plan.request.destination.toLowerCase()}_animation_bundle.json`,
                        JSON.stringify(animation, null, 2),
                        "application/json",
                      )
                    }
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-sky-200 bg-white px-4 py-3 text-sm font-medium text-sky-700 transition hover:border-sky-400 hover:bg-sky-50 dark:border-sky-700 dark:bg-slate-900 dark:text-sky-300"
                  >
                    <FileDown className="h-4 w-4" /> 下载动画 JSON
                  </button>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-900/40">
                  <button
                    onClick={() =>
                      downloadText(
                        `${plan.request.destination.toLowerCase()}_trip_guide.md`,
                        plan.summary_markdown ||
                          "# 旅行手册\n\n当前案例未提供 markdown 摘要。",
                        "text/markdown",
                      )
                    }
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-emerald-200 bg-white px-4 py-3 text-sm font-medium text-emerald-700 transition hover:border-emerald-400 hover:bg-emerald-50 dark:border-emerald-700 dark:bg-slate-900 dark:text-emerald-300"
                  >
                    <FileDown className="h-4 w-4" /> 下载旅行手册
                  </button>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4 dark:border-slate-700 dark:bg-slate-900/40">
                <div className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                  Markdown 预览
                </div>
                <pre className="max-h-[56vh] overflow-auto rounded-xl border border-slate-200 bg-white p-3 whitespace-pre-wrap text-xs leading-5 text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
                  {plan.summary_markdown || "当前案例未提供 markdown 摘要。"}
                </pre>
              </div>
            </div>
          )}
        </div>

        {/* Risk & Validation Drawer (portaled to body) */}
        {createPortal(
          <div
            className={`fixed inset-0 z-[100] transition-colors duration-300 ease-out ${showRiskModal ? "bg-slate-950/55 pointer-events-auto" : "bg-transparent pointer-events-none"}`}
            onClick={() => setShowRiskModal(false)}
          >
            <div
              className={`fixed inset-y-0 right-0 h-screen w-full max-w-2xl border-l border-slate-200/80 bg-white shadow-2xl transition-transform duration-300 ease-out dark:border-slate-700/80 dark:bg-slate-900 ${showRiskModal ? "translate-x-0" : "translate-x-full"}`}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex h-full flex-col overflow-hidden">
                {/* Header */}
                <div className="flex items-center gap-3 border-b border-amber-100 bg-gradient-to-r from-amber-50 to-orange-50 px-6 py-4 dark:border-amber-900/40 dark:from-amber-950/60 dark:to-orange-950/60">
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-amber-100 text-xl dark:bg-amber-900/60">
                    ⚠️
                  </div>
                  <div className="flex-1">
                    <h3 className="text-base font-bold text-slate-900 dark:text-slate-100">
                      校验与风险详情
                    </h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      共 {riskCount} 项提示
                    </p>
                  </div>
                  {/* Score & Revised badges */}
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1.5 rounded-full bg-indigo-100 px-3 py-1 dark:bg-indigo-900/50">
                      <ShieldCheck className="h-3.5 w-3.5 text-indigo-600 dark:text-indigo-400" />
                      <span className="text-xs font-semibold text-indigo-700 dark:text-indigo-300">
                        评分 {plan.final_score.toFixed(0)}
                      </span>
                    </div>
                    <div
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        plan.was_revised
                          ? "bg-teal-100 text-teal-700 dark:bg-teal-900/50 dark:text-teal-300"
                          : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400"
                      }`}
                    >
                      {plan.was_revised ? "已自动修正" : "未触发修正"}
                    </div>
                  </div>
                  <button
                    onClick={() => setShowRiskModal(false)}
                    className="ml-2 rounded-full p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-700"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                {/* Scrollable body */}
                <div className="flex-1 overflow-y-auto px-6 py-5 [overflow-wrap:anywhere]">
                  {(() => {
                    const severityMap: Record<
                      string,
                      {
                        bg: string;
                        text: string;
                        dot: string;
                        badge: string;
                        label: string;
                        icon: string;
                      }
                    > = {
                      high: {
                        bg: "bg-rose-50 border-rose-200 dark:bg-rose-950/40 dark:border-rose-800",
                        text: "text-rose-700 dark:text-rose-300",
                        dot: "bg-rose-500",
                        badge:
                          "bg-white/70 text-rose-700 dark:bg-black/20 dark:text-rose-300",
                        label: "严重",
                        icon: "🔴",
                      },
                      error: {
                        bg: "bg-rose-50 border-rose-200 dark:bg-rose-950/40 dark:border-rose-800",
                        text: "text-rose-700 dark:text-rose-300",
                        dot: "bg-rose-500",
                        badge:
                          "bg-white/70 text-rose-700 dark:bg-black/20 dark:text-rose-300",
                        label: "严重",
                        icon: "🔴",
                      },
                      medium: {
                        bg: "bg-amber-50 border-amber-200 dark:bg-amber-950/40 dark:border-amber-800",
                        text: "text-amber-700 dark:text-amber-300",
                        dot: "bg-amber-500",
                        badge:
                          "bg-white/70 text-amber-700 dark:bg-black/20 dark:text-amber-300",
                        label: "警告",
                        icon: "🟡",
                      },
                      warning: {
                        bg: "bg-amber-50 border-amber-200 dark:bg-amber-950/40 dark:border-amber-800",
                        text: "text-amber-700 dark:text-amber-300",
                        dot: "bg-amber-500",
                        badge:
                          "bg-white/70 text-amber-700 dark:bg-black/20 dark:text-amber-300",
                        label: "警告",
                        icon: "🟡",
                      },
                      low: {
                        bg: "bg-blue-50 border-blue-200 dark:bg-blue-950/40 dark:border-blue-800",
                        text: "text-blue-700 dark:text-blue-300",
                        dot: "bg-blue-500",
                        badge:
                          "bg-white/70 text-blue-700 dark:bg-black/20 dark:text-blue-300",
                        label: "提示",
                        icon: "🔵",
                      },
                      info: {
                        bg: "bg-blue-50 border-blue-200 dark:bg-blue-950/40 dark:border-blue-800",
                        text: "text-blue-700 dark:text-blue-300",
                        dot: "bg-blue-500",
                        badge:
                          "bg-white/70 text-blue-700 dark:bg-black/20 dark:text-blue-300",
                        label: "提示",
                        icon: "🔵",
                      },
                    };

                    if (riskItems.length === 0) {
                      return (
                        <div className="flex flex-col items-center gap-3 py-10 text-slate-400 dark:text-slate-500">
                          <span className="text-4xl">✅</span>
                          <p className="text-sm">
                            未发现明显约束冲突，行程规划顺利完成。
                          </p>
                        </div>
                      );
                    }

                    return (
                      <div className="space-y-3">
                        {riskItems.map((item, i) => {
                          const s =
                            severityMap[item.severity] ?? severityMap["info"];
                          return (
                            <div
                              key={i}
                              className={`rounded-2xl border p-4 ${s.bg}`}
                            >
                              {/* Tags row */}
                              <div className="mb-2 flex flex-wrap items-center gap-2">
                                <span
                                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${s.badge}`}
                                >
                                  <span
                                    className={`h-1.5 w-1.5 rounded-full ${s.dot}`}
                                  />
                                  {s.icon} {s.label}
                                </span>
                                {item.category && (
                                  <span className="rounded-full bg-white/70 px-2.5 py-0.5 text-xs font-medium text-slate-600 dark:bg-black/20 dark:text-slate-300">
                                    {item.category}
                                  </span>
                                )}
                                {item.day != null && (
                                  <span className="rounded-full bg-white/70 px-2.5 py-0.5 text-xs text-slate-500 dark:bg-black/20 dark:text-slate-400">
                                    第 {item.day} 天
                                  </span>
                                )}
                              </div>
                              {/* Message */}
                              <p
                                className={`text-sm leading-relaxed ${s.text}`}
                              >
                                {item.message}
                              </p>
                              {/* Suggestion */}
                              {item.suggestion ? (
                                <div className="mt-3 flex items-start gap-2 rounded-xl bg-white/60 px-3 py-2 dark:bg-black/15">
                                  <span className="flex-shrink-0 text-sm text-teal-500">
                                    💡
                                  </span>
                                  <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-400">
                                    <span className="font-semibold text-teal-700 dark:text-teal-400">
                                      修正建议：
                                    </span>
                                    {item.suggestion}
                                  </p>
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    );
                  })()}
                </div>

                {/* Footer */}
                <div className="border-t border-slate-100 px-6 py-3 dark:border-slate-800">
                  <button
                    onClick={() => setShowRiskModal(false)}
                    className="w-full rounded-xl bg-slate-100 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                  >
                    关闭
                  </button>
                </div>
              </div>
            </div>
          </div>,
          document.body,
        )}
      </div>
    </div>
  );
}
