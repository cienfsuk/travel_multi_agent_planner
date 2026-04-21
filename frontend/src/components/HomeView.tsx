import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  ArrowLeftRight,
  Calendar,
  Coins,
  MapPinned,
  Navigation,
  NotebookText,
  PlaneTakeoff,
  Settings2,
  Users,
} from "lucide-react";
import type { TripRequest } from "../types/api";

const INTEREST_OPTIONS = [
  { label: "文化", value: "culture" },
  { label: "美食", value: "food" },
  { label: "自然", value: "nature" },
  { label: "历史", value: "history" },
  { label: "摄影", value: "photography" },
  { label: "购物", value: "shopping" },
  { label: "茶文化", value: "tea" },
  { label: "夜游", value: "night" },
  { label: "慢生活", value: "relaxed" },
];

const TASTE_OPTIONS = ["酸", "甜", "苦", "辣", "鲜", "清淡"];
const TITLE_GRADIENT_TEXT = "游策AI ";
const TITLE_PLAIN_TEXT = "旅行规划系统";
const FULL_TITLE_TEXT = `${TITLE_GRADIENT_TEXT}${TITLE_PLAIN_TEXT}`;
const MIN_READABLE_SCALE = 0.85;

const PRESET_REQUESTS: Array<{ label: string; value: TripRequest }> = [
  {
    label: "🏙️ 上海-杭州 5天，1500元",
    value: {
      destination: "杭州",
      days: 5,
      budget: 1500,
      origin: "上海",
      departure_date: "",
      traveler_count: 2,
      interests: ["culture", "food"],
      preferred_areas: [],
      avoid_tags: [],
      food_tastes: ["鲜", "清淡"],
      style: "balanced",
      food_budget_preference: "balanced",
      hotel_budget_preference: "balanced",
      must_have_hotel_area: "西湖",
      travel_note_style: "小红书风格",
      additional_notes: "时间充裕，可多体验文化景点",
    },
  },
  {
    label: "🏔️ 成都-都江堰 4天，2000元",
    value: {
      destination: "都江堰",
      days: 4,
      budget: 2000,
      origin: "成都",
      departure_date: "",
      traveler_count: 2,
      interests: ["nature", "history"],
      preferred_areas: [],
      avoid_tags: [],
      food_tastes: ["辣", "鲜"],
      style: "balanced",
      food_budget_preference: "balanced",
      hotel_budget_preference: "balanced",
      must_have_hotel_area: "景区周边",
      travel_note_style: "小红书风格",
      additional_notes: "重点体验山水和古建筑",
    },
  },
  {
    label: "🏖️ 西安-兰州 7天，3000元",
    value: {
      destination: "兰州",
      days: 7,
      budget: 3000,
      origin: "西安",
      departure_date: "",
      traveler_count: 1,
      interests: ["history", "photography"],
      preferred_areas: [],
      avoid_tags: [],
      food_tastes: ["辣", "鲜"],
      style: "dense",
      food_budget_preference: "budget",
      hotel_budget_preference: "budget",
      must_have_hotel_area: "",
      travel_note_style: "小红书风格",
      additional_notes: "充实紧凑的古迹之旅",
    },
  },
];

interface Props {
  onStart: (request: TripRequest) => void;
  theme?: "light" | "dark";
}

interface NumericDrafts {
  days: string;
  budget: string;
  travelerCount: string;
}

const panelClassName =
  "rounded-[20px] border border-white/60 bg-white/82 p-3.5 shadow-[0_18px_54px_rgba(15,23,42,0.08)] backdrop-blur dark:border-slate-700/80 dark:bg-slate-900/78 dark:shadow-[0_20px_60px_rgba(2,6,23,0.52)]";
const fieldClassName =
  "group rounded-xl border border-slate-200/80 bg-white/85 px-3 py-2 shadow-sm transition-all duration-200 hover:border-sky-300 focus-within:border-sky-400 dark:border-slate-700 dark:bg-slate-900/75 dark:hover:border-sky-500/70 dark:focus-within:border-sky-400";
const centeredFieldClassName = `${fieldClassName} flex flex-col justify-center text-center`;
const inputClassName =
  "mt-1 w-full border-none bg-transparent p-0 text-[13px] font-medium leading-6 text-center text-slate-900 outline-none placeholder:text-slate-400 dark:text-slate-100 dark:placeholder:text-slate-500";
const selectClassName = `${inputClassName} appearance-none pr-4`;
const numericFieldClassName =
  "rounded-xl border border-slate-200/80 bg-white/90 px-4 py-3 text-left shadow-sm transition-all duration-200 hover:border-sky-300 focus-within:border-sky-400 dark:border-slate-700 dark:bg-slate-900/80 dark:hover:border-sky-500/70 dark:focus-within:border-sky-400";
const numericInputClassName =
  "mt-2 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-semibold leading-5 text-slate-900 outline-none transition-colors [appearance:textfield] placeholder:text-slate-400 focus:border-sky-400 focus:bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-sky-400 dark:focus:bg-slate-900 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";

function SectionHeader({
  title,
  description,
  icon,
}: {
  title: string;
  description: string;
  icon: ReactNode;
}) {
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500 to-cyan-400 text-white">
        {icon}
      </div>
      <div>
        <h2 className="text-[15px] font-semibold text-slate-900 dark:text-white">
          {title}
        </h2>
        <p className="mt-0.5 text-[11px] leading-4 text-slate-500 dark:text-slate-400">
          {description}
        </p>
      </div>
    </div>
  );
}

function parsePositiveNumber(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parsePositiveInteger(value: string) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function createNumericDrafts(
  request: Pick<TripRequest, "days" | "budget" | "traveler_count">,
): NumericDrafts {
  return {
    days: String(request.days),
    budget: String(request.budget),
    travelerCount: String(request.traveler_count ?? 1),
  };
}

export default function HomeView({ onStart }: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [fitScale, setFitScale] = useState(1);
  const [allowScrollFallback, setAllowScrollFallback] = useState(false);
  const [typedTitle, setTypedTitle] = useState("");
  const [form, setForm] = useState<TripRequest>({
    destination: "",
    days: 2,
    budget: 1000,
    origin: "",
    departure_date: "",
    traveler_count: 1,
    interests: [],
    preferred_areas: [],
    avoid_tags: [],
    food_tastes: [],
    style: "balanced",
    food_budget_preference: "balanced",
    hotel_budget_preference: "balanced",
    must_have_hotel_area: "",
    travel_note_style: "",
    additional_notes: "",
  });
  const [numericDrafts, setNumericDrafts] = useState<NumericDrafts>({
    days: "2",
    budget: "1000",
    travelerCount: "1",
  });

  useEffect(() => {
    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setTypedTitle(FULL_TITLE_TEXT.slice(0, index));
      if (index >= FULL_TITLE_TEXT.length) {
        window.clearInterval(timer);
      }
    }, 120);

    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const updateScale = () => {
      const viewport = viewportRef.current;
      const content = contentRef.current;
      if (!viewport || !content) {
        return;
      }

      const availableHeight = Math.max(viewport.clientHeight - 32, 0);
      const contentHeight = content.scrollHeight;
      if (contentHeight <= 0) {
        setFitScale(1);
        setAllowScrollFallback(false);
        return;
      }

      const naturalScale = Math.min(1, availableHeight / contentHeight);
      const scale = Math.max(MIN_READABLE_SCALE, naturalScale);
      setFitScale(scale);
      setAllowScrollFallback(naturalScale < MIN_READABLE_SCALE);
    };

    updateScale();
    const raf = window.requestAnimationFrame(updateScale);
    const observer = new ResizeObserver(updateScale);
    if (viewportRef.current) observer.observe(viewportRef.current);
    if (contentRef.current) observer.observe(contentRef.current);
    window.addEventListener("resize", updateScale);

    return () => {
      window.cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener("resize", updateScale);
    };
  }, [typedTitle]);

  const handleStart = () => {
    if (!form.destination.trim()) {
      return;
    }

    const normalizedDays =
      parsePositiveInteger(numericDrafts.days) ?? form.days;
    const normalizedBudget =
      parsePositiveNumber(numericDrafts.budget) ?? form.budget;
    const normalizedTravelerCount =
      parsePositiveInteger(numericDrafts.travelerCount) ??
      form.traveler_count ??
      1;

    setForm((prev) => ({
      ...prev,
      days: normalizedDays,
      budget: normalizedBudget,
      traveler_count: normalizedTravelerCount,
    }));
    setNumericDrafts({
      days: String(normalizedDays),
      budget: String(normalizedBudget),
      travelerCount: String(normalizedTravelerCount),
    });

    onStart({
      ...form,
      days: normalizedDays,
      budget: normalizedBudget,
      traveler_count: normalizedTravelerCount,
      destination: form.destination.trim(),
      origin: form.origin.trim(),
    });
  };

  const handleNumericDraftChange = (
    field: keyof NumericDrafts,
    value: string,
  ) => {
    setNumericDrafts((prev) => ({ ...prev, [field]: value }));

    if (field === "days") {
      const parsed = parsePositiveInteger(value);
      if (parsed !== null) {
        setForm((prev) => ({ ...prev, days: parsed }));
      }
      return;
    }

    if (field === "budget") {
      const parsed = parsePositiveNumber(value);
      if (parsed !== null) {
        setForm((prev) => ({ ...prev, budget: parsed }));
      }
      return;
    }

    const parsed = parsePositiveInteger(value);
    if (parsed !== null) {
      setForm((prev) => ({ ...prev, traveler_count: parsed }));
    }
  };

  const handleNumericDraftBlur = (field: keyof NumericDrafts) => {
    setNumericDrafts((prev) => {
      if (field === "days") {
        const parsed = parsePositiveInteger(prev.days);
        return { ...prev, days: String(parsed ?? form.days) };
      }

      if (field === "budget") {
        const parsed = parsePositiveNumber(prev.budget);
        return { ...prev, budget: String(parsed ?? form.budget) };
      }

      const parsed = parsePositiveInteger(prev.travelerCount);
      return {
        ...prev,
        travelerCount: String(parsed ?? form.traveler_count ?? 1),
      };
    });
  };

  const handleSwapCities = () => {
    setForm((prev) => ({
      ...prev,
      origin: prev.destination,
      destination: prev.origin,
    }));
  };

  const toggleInterest = (value: string) => {
    const current = form.interests ?? [];
    const next = current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value];
    setForm((prev) => ({ ...prev, interests: next }));
  };

  const toggleTaste = (value: string) => {
    const current = form.food_tastes ?? [];
    const next = current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value];
    setForm((prev) => ({ ...prev, food_tastes: next }));
  };

  const travelSummary = [
    form.origin || "出发地待定",
    form.destination || "目的地待定",
    `${form.days} 天`,
    `${form.budget} 元`,
  ].join(" · ");

  return (
    <div
      ref={viewportRef}
      className={`relative flex h-[calc(100svh-64px)] items-start justify-center bg-[radial-gradient(circle_at_top_left,_rgba(56,189,248,0.16),_transparent_30%),radial-gradient(circle_at_82%_18%,_rgba(14,165,233,0.12),_transparent_20%),linear-gradient(180deg,_rgba(248,250,252,0.98),_rgba(239,246,255,0.95))] px-3 pb-6 pt-3 transition-colors dark:bg-[radial-gradient(circle_at_top_left,_rgba(14,165,233,0.16),_transparent_24%),radial-gradient(circle_at_82%_18%,_rgba(34,197,94,0.1),_transparent_20%),linear-gradient(180deg,_rgba(2,6,23,0.98),_rgba(15,23,42,0.95))] md:px-4 md:pb-6 md:pt-4 ${allowScrollFallback ? "overflow-y-auto overflow-x-hidden" : "overflow-hidden"}`}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-56 bg-[linear-gradient(180deg,rgba(255,255,255,0.3),transparent)] dark:bg-[linear-gradient(180deg,rgba(15,23,42,0.32),transparent)]" />

      <div
        ref={contentRef}
        className="relative mx-auto flex w-full max-w-6xl flex-col items-center justify-start gap-3 pb-2 text-center md:pb-3"
        style={{
          transform: `scale(${fitScale})`,
          transformOrigin: "top center",
        }}
      >
        <div className="mb-5 flex flex-col items-center gap-2.5 text-center md:mb-6">
          <PlaneTakeoff
            className="mb-1 h-16 w-16 text-sky-500 transition-transform hover:scale-105 hover:text-cyan-500 md:h-20 md:w-20"
            strokeWidth={1.5}
          />
          <h1 className="text-2xl font-extrabold tracking-tight text-slate-900 dark:text-white md:text-3xl">
            <span className="bg-gradient-to-r from-sky-500 via-cyan-500 to-emerald-500 bg-clip-text text-transparent">
              {typedTitle.slice(0, TITLE_GRADIENT_TEXT.length)}
            </span>
            {typedTitle.slice(TITLE_GRADIENT_TEXT.length)}
            {typedTitle.length < FULL_TITLE_TEXT.length && (
              <span className="ml-0.5 inline-block h-[1em] w-[1px] animate-pulse align-[-0.1em] bg-slate-500" />
            )}
          </h1>
          <div className="mt-1.5 rounded-full border border-white/60 bg-white/80 px-3 py-1 text-[11px] text-slate-600 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/75 dark:text-slate-300">
            {travelSummary}
          </div>
        </div>

        <div className="w-full">
          <div className={`${panelClassName} flex flex-col gap-3`}>
            <div className="grid gap-3 lg:grid-cols-2">
              <section className="flex flex-col gap-2.5 rounded-2xl border border-sky-100/70 bg-gradient-to-br from-white/95 via-sky-50/70 to-cyan-50/50 p-3 dark:border-slate-700 dark:from-slate-900/80 dark:via-slate-900/75 dark:to-slate-800/60">
                <SectionHeader
                  title="关键信息"
                  description="基础输入"
                  icon={<MapPinned className="h-4 w-4" />}
                />

                <div className="grid gap-2.5">
                  <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-stretch">
                    <label
                      className={`${centeredFieldClassName} min-h-[88px] px-4 py-3`}
                    >
                      <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                        出发地
                      </div>
                      <input
                        type="text"
                        className={inputClassName}
                        placeholder="例如：上海"
                        value={form.origin}
                        onChange={(e) =>
                          setForm((prev) => ({
                            ...prev,
                            origin: e.target.value,
                          }))
                        }
                      />
                    </label>

                    <div className="flex items-center justify-center py-1 sm:py-0">
                      <button
                        type="button"
                        onClick={handleSwapCities}
                        className="flex h-10 w-10 items-center justify-center rounded-xl border border-sky-200 bg-white text-sky-600 shadow-sm transition-all hover:-translate-y-0.5 hover:border-sky-300 hover:bg-sky-50 dark:border-slate-700 dark:bg-slate-900 dark:text-sky-300 dark:hover:border-sky-500/40 dark:hover:bg-slate-800"
                        title="交换出发地和目的地"
                      >
                        <ArrowLeftRight className="h-4 w-4" />
                      </button>
                    </div>

                    <label
                      className={`${centeredFieldClassName} min-h-[88px] px-4 py-3`}
                    >
                      <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                        目的地
                      </div>
                      <input
                        type="text"
                        className={inputClassName}
                        placeholder="例如：杭州"
                        value={form.destination}
                        onChange={(e) =>
                          setForm((prev) => ({
                            ...prev,
                            destination: e.target.value,
                          }))
                        }
                      />
                    </label>
                  </div>

                  <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-[1.6fr_1fr_1fr_1fr]">
                    <label
                      className={`${centeredFieldClassName} min-h-[88px] px-4 py-3`}
                    >
                      <div className="flex items-center justify-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                        <Calendar className="h-3 w-3" />
                        出发日期
                      </div>
                      <input
                        type="date"
                        className={inputClassName}
                        value={form.departure_date ?? ""}
                        onChange={(e) =>
                          setForm((prev) => ({
                            ...prev,
                            departure_date: e.target.value,
                          }))
                        }
                      />
                    </label>

                    <label className={`${numericFieldClassName} min-h-[88px]`}>
                      <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                        天数
                      </div>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        inputMode="numeric"
                        className={numericInputClassName}
                        placeholder="输入天数"
                        value={numericDrafts.days}
                        onChange={(e) =>
                          handleNumericDraftChange("days", e.target.value)
                        }
                        onBlur={() => handleNumericDraftBlur("days")}
                      />
                    </label>

                    <label className={`${numericFieldClassName} min-h-[88px]`}>
                      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                        <Coins className="h-3 w-3" />
                        预算
                      </div>
                      <input
                        type="number"
                        min={0.01}
                        step="any"
                        inputMode="numeric"
                        className={numericInputClassName}
                        placeholder="输入预算"
                        value={numericDrafts.budget}
                        onChange={(e) =>
                          handleNumericDraftChange("budget", e.target.value)
                        }
                        onBlur={() => handleNumericDraftBlur("budget")}
                      />
                    </label>

                    <label className={`${numericFieldClassName} min-h-[88px]`}>
                      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                        <Users className="h-3 w-3" />
                        同行人数
                      </div>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        inputMode="numeric"
                        className={numericInputClassName}
                        placeholder="输入人数"
                        value={numericDrafts.travelerCount}
                        onChange={(e) =>
                          handleNumericDraftChange(
                            "travelerCount",
                            e.target.value,
                          )
                        }
                        onBlur={() => handleNumericDraftBlur("travelerCount")}
                      />
                    </label>
                  </div>
                </div>
              </section>

              <section className="flex flex-col gap-2.5 rounded-2xl border border-emerald-100/70 bg-gradient-to-br from-white/95 via-emerald-50/70 to-cyan-50/50 p-3 dark:border-slate-700 dark:from-slate-900/80 dark:via-slate-900/75 dark:to-slate-800/60">
                <SectionHeader
                  title="偏好设置"
                  description="路线与消费偏好"
                  icon={<Settings2 className="h-4 w-4" />}
                />

                <div className="grid grid-cols-2 gap-2.5">
                  <label className={centeredFieldClassName}>
                    <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                      旅行节奏
                    </div>
                    <select
                      className={selectClassName}
                      value={form.style ?? "balanced"}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          style: e.target.value as TripRequest["style"],
                        }))
                      }
                    >
                      <option value="relaxed">轻松</option>
                      <option value="balanced">均衡</option>
                      <option value="dense">紧凑</option>
                    </select>
                  </label>

                  <label className={centeredFieldClassName}>
                    <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                      攻略风格
                    </div>
                    <select
                      className={selectClassName}
                      value={form.travel_note_style ?? "小红书风格"}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          travel_note_style: e.target.value,
                        }))
                      }
                    >
                      <option value="小红书风格">小红书</option>
                      <option value="预算友好">预算友好</option>
                      <option value="城市漫游">城市漫游</option>
                    </select>
                  </label>

                  <label className={centeredFieldClassName}>
                    <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                      餐饮预算
                    </div>
                    <select
                      className={selectClassName}
                      value={form.food_budget_preference ?? "balanced"}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          food_budget_preference: e.target
                            .value as TripRequest["food_budget_preference"],
                        }))
                      }
                    >
                      <option value="budget">平价</option>
                      <option value="balanced">均衡</option>
                      <option value="premium">高档</option>
                    </select>
                  </label>

                  <label className={centeredFieldClassName}>
                    <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                      酒店预算
                    </div>
                    <select
                      className={selectClassName}
                      value={form.hotel_budget_preference ?? "balanced"}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          hotel_budget_preference: e.target
                            .value as TripRequest["hotel_budget_preference"],
                        }))
                      }
                    >
                      <option value="budget">平价</option>
                      <option value="balanced">均衡</option>
                      <option value="premium">高档</option>
                    </select>
                  </label>
                </div>

                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  <div className={`${fieldClassName} min-h-[84px]`}>
                    <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                      兴趣偏好
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {INTEREST_OPTIONS.map((item) => {
                        const active = (form.interests ?? []).includes(
                          item.value,
                        );
                        return (
                          <button
                            key={item.value}
                            type="button"
                            onClick={() => toggleInterest(item.value)}
                            className={`rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                              active
                                ? "border-sky-500 bg-sky-500 text-white"
                                : "border-slate-200 bg-slate-50 text-slate-700 hover:border-sky-200 hover:bg-sky-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-sky-500/40 dark:hover:bg-slate-700"
                            }`}
                          >
                            {item.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className={`${fieldClassName} min-h-[84px]`}>
                    <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                      口味偏好
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {TASTE_OPTIONS.map((taste) => {
                        const active = (form.food_tastes ?? []).includes(taste);
                        return (
                          <button
                            key={taste}
                            type="button"
                            onClick={() => toggleTaste(taste)}
                            className={`rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                              active
                                ? "border-emerald-500 bg-emerald-500 text-white"
                                : "border-slate-200 bg-slate-50 text-slate-700 hover:border-emerald-200 hover:bg-emerald-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-emerald-500/40 dark:hover:bg-slate-700"
                            }`}
                          >
                            {taste}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </section>
            </div>

            <div className="grid gap-3 lg:grid-cols-3">
              <section className="flex flex-col gap-2.5 rounded-2xl border border-amber-100/70 bg-gradient-to-br from-white/95 via-amber-50/70 to-orange-50/50 p-3 dark:border-slate-700 dark:from-slate-900/80 dark:via-slate-900/75 dark:to-slate-800/60 lg:col-span-2">
                <SectionHeader
                  title="补充说明"
                  description="路线偏好或限制条件"
                  icon={<NotebookText className="h-4 w-4" />}
                />

                <label className={`${fieldClassName} min-h-[92px]`}>
                  <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400 dark:text-slate-500">
                    你的额外需求
                  </div>
                  <textarea
                    className={`${inputClassName} h-14 resize-none leading-5`}
                    placeholder="例如：尽量少赶路，优先美食与拍照点。"
                    value={form.additional_notes ?? ""}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        additional_notes: e.target.value,
                      }))
                    }
                  />
                </label>
              </section>

              <section className="flex flex-col gap-2.5 rounded-2xl border border-dashed border-slate-200/80 bg-gradient-to-br from-slate-50 via-white to-slate-50 p-3 dark:border-slate-700 dark:from-slate-900 dark:via-slate-900/95 dark:to-slate-800">
                <SectionHeader
                  title="快捷预设"
                  description="一键填入"
                  icon={<Navigation className="h-4 w-4" />}
                />

                <div className="flex flex-wrap gap-1.5">
                  {PRESET_REQUESTS.map((preset, index) => (
                    <button
                      key={index}
                      type="button"
                      onClick={() => {
                        setForm(preset.value);
                        setNumericDrafts(createNumericDrafts(preset.value));
                      }}
                      className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-700 transition-colors hover:border-sky-200 hover:bg-sky-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:border-sky-500 dark:hover:bg-slate-700"
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>

                <button
                  type="button"
                  onClick={handleStart}
                  className="mt-auto inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-sky-500 via-cyan-500 to-emerald-500 px-4 text-sm font-semibold text-white shadow-[0_12px_28px_rgba(14,165,233,0.28)] transition-all hover:-translate-y-0.5 hover:shadow-[0_16px_34px_rgba(14,165,233,0.34)] disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={!form.destination.trim()}
                >
                  开始规划
                  <Navigation className="h-3.5 w-3.5" />
                </button>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
