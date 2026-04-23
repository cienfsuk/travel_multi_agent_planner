import { useMemo, useState } from "react";
import { MapPinned, NotebookText, PlaneTakeoff, Settings2 } from "lucide-react";
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

const PRESET_REQUESTS: Array<{ label: string; value: TripRequest }> = [
  {
    label: "上海-杭州 5天 / 1500",
    value: {
      destination: "杭州",
      days: 5,
      budget: 1500,
      origin: "上海",
      departure_date: "",
      traveler_count: 2,
      interests: ["culture", "food"],
      preferred_areas: ["西湖"],
      avoid_tags: [],
      food_tastes: ["鲜", "清淡"],
      style: "balanced",
      food_budget_preference: "balanced",
      hotel_budget_preference: "balanced",
      must_have_hotel_area: "西湖",
      travel_note_style: "小红书风格",
      additional_notes: "行程不要太赶，优先安排核心景点。",
    },
  },
  {
    label: "上海-南京 3天 / 1500",
    value: {
      destination: "南京",
      days: 3,
      budget: 1500,
      origin: "上海",
      departure_date: "",
      traveler_count: 2,
      interests: ["culture", "food"],
      preferred_areas: ["玄武湖", "钟山风景区"],
      avoid_tags: [],
      food_tastes: ["鲜", "辣"],
      style: "balanced",
      food_budget_preference: "balanced",
      hotel_budget_preference: "balanced",
      must_have_hotel_area: "玄武湖",
      travel_note_style: "小红书风格",
      additional_notes: "第一天必须包含玄武湖和钟山风景区。",
    },
  },
];

interface Props {
  onStart: (request: TripRequest) => void;
  theme?: "light" | "dark";
}

function parseDelimitedList(value: string): string[] {
  return value
    .split(/[，,、;；\n]+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function stringifyDelimitedList(values?: string[]): string {
  return (values ?? []).join("，");
}

export default function HomeView({ onStart }: Props) {
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
    travel_note_style: "小红书风格",
    additional_notes: "",
  });
  const [preferredAreasDraft, setPreferredAreasDraft] = useState("");
  const [avoidTagsDraft, setAvoidTagsDraft] = useState("");

  const canStart = useMemo(() => {
    return form.origin?.trim() && form.destination?.trim() && form.days > 0 && form.budget > 0;
  }, [form]);

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

  const applyPreset = (value: TripRequest) => {
    setForm(value);
    setPreferredAreasDraft(stringifyDelimitedList(value.preferred_areas));
    setAvoidTagsDraft(stringifyDelimitedList(value.avoid_tags));
  };

  const handleStart = () => {
    if (!canStart) return;
    onStart({
      ...form,
      origin: (form.origin ?? "").trim(),
      destination: (form.destination ?? "").trim(),
      days: Math.max(1, Number(form.days) || 1),
      budget: Math.max(1, Number(form.budget) || 1),
      traveler_count: Math.max(1, Number(form.traveler_count ?? 1) || 1),
      preferred_areas: parseDelimitedList(preferredAreasDraft),
      avoid_tags: parseDelimitedList(avoidTagsDraft),
      must_have_hotel_area: (form.must_have_hotel_area ?? "").trim(),
      additional_notes: (form.additional_notes ?? "").trim(),
    });
  };

  return (
    <div className="flex h-[calc(100svh-64px)] items-start justify-center overflow-y-auto bg-gradient-to-b from-slate-50 to-sky-50 px-4 py-4 dark:from-slate-950 dark:to-slate-900">
      <div className="w-full max-w-6xl space-y-4">
        <div className="rounded-2xl border border-white/60 bg-white/85 p-5 shadow-sm dark:border-slate-700 dark:bg-slate-900/80">
          <div className="flex items-center gap-3">
            <PlaneTakeoff className="h-8 w-8 text-sky-500" />
            <div>
              <h1 className="text-2xl font-extrabold text-slate-900 dark:text-white">
                游策 AI 旅行规划系统
              </h1>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                完整填写约束项，系统会按硬约束规划路线、酒店与规避条件
              </p>
            </div>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-2xl border border-sky-100 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
              <MapPinned className="h-4 w-4 text-sky-500" />
              基础信息
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">出发地</span>
                <input
                  type="text"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-sky-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.origin}
                  onChange={(e) => setForm((prev) => ({ ...prev, origin: e.target.value }))}
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">目的地</span>
                <input
                  type="text"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-sky-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.destination}
                  onChange={(e) => setForm((prev) => ({ ...prev, destination: e.target.value }))}
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">出发日期</span>
                <input
                  type="date"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-sky-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.departure_date ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, departure_date: e.target.value }))}
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">天数</span>
                <input
                  type="number"
                  min={1}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-sky-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.days > 0 ? String(form.days) : ""}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      days: e.target.value === "" ? 0 : Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">预算（元）</span>
                <input
                  type="number"
                  min={1}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-sky-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.budget}
                  onChange={(e) => setForm((prev) => ({ ...prev, budget: Number(e.target.value) }))}
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">出行人数</span>
                <input
                  type="number"
                  min={1}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-sky-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.traveler_count ?? 1}
                  onChange={(e) => setForm((prev) => ({ ...prev, traveler_count: Number(e.target.value) }))}
                />
              </label>
            </div>
          </section>

          <section className="rounded-2xl border border-emerald-100 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
              <Settings2 className="h-4 w-4 text-emerald-500" />
              偏好设置
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">旅行节奏</span>
                <select
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.style ?? "balanced"}
                  onChange={(e) => setForm((prev) => ({ ...prev, style: e.target.value as TripRequest["style"] }))}
                >
                  <option value="relaxed">轻松</option>
                  <option value="balanced">均衡</option>
                  <option value="dense">紧凑</option>
                </select>
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">攻略风格</span>
                <select
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.travel_note_style ?? "小红书风格"}
                  onChange={(e) => setForm((prev) => ({ ...prev, travel_note_style: e.target.value }))}
                >
                  <option value="小红书风格">小红书风格</option>
                  <option value="预算友好">预算友好</option>
                  <option value="城市漫游">城市漫游</option>
                </select>
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">餐饮预算偏好</span>
                <select
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.food_budget_preference ?? "balanced"}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      food_budget_preference: e.target.value as TripRequest["food_budget_preference"],
                    }))
                  }
                >
                  <option value="budget">平价</option>
                  <option value="balanced">均衡</option>
                  <option value="premium">高档</option>
                </select>
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">酒店预算偏好</span>
                <select
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900"
                  value={form.hotel_budget_preference ?? "balanced"}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      hotel_budget_preference: e.target.value as TripRequest["hotel_budget_preference"],
                    }))
                  }
                >
                  <option value="budget">平价</option>
                  <option value="balanced">均衡</option>
                  <option value="premium">高档</option>
                </select>
              </label>
            </div>
            <div className="mt-3 space-y-2">
              <div className="text-xs text-slate-500">兴趣偏好</div>
              <div className="flex flex-wrap gap-2">
                {INTEREST_OPTIONS.map((item) => {
                  const active = (form.interests ?? []).includes(item.value);
                  return (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => toggleInterest(item.value)}
                      className={`rounded-full border px-2.5 py-1 text-xs ${
                        active
                          ? "border-sky-500 bg-sky-500 text-white"
                          : "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                      }`}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="mt-3 space-y-2">
              <div className="text-xs text-slate-500">口味偏好</div>
              <div className="flex flex-wrap gap-2">
                {TASTE_OPTIONS.map((taste) => {
                  const active = (form.food_tastes ?? []).includes(taste);
                  return (
                    <button
                      key={taste}
                      type="button"
                      onClick={() => toggleTaste(taste)}
                      className={`rounded-full border px-2.5 py-1 text-xs ${
                        active
                          ? "border-emerald-500 bg-emerald-500 text-white"
                          : "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                      }`}
                    >
                      {taste}
                    </button>
                  );
                })}
              </div>
            </div>
          </section>
        </div>

        <section className="rounded-2xl border border-amber-100 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
            <NotebookText className="h-4 w-4 text-amber-500" />
            补充说明（路线偏好与限制条件）
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="text-slate-500">偏好区域（逗号分隔）</span>
              <input
                type="text"
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-amber-400 dark:border-slate-700 dark:bg-slate-900"
                placeholder="例如：玄武湖，钟山风景区"
                value={preferredAreasDraft}
                onChange={(e) => setPreferredAreasDraft(e.target.value)}
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-slate-500">规避标签（逗号分隔）</span>
              <input
                type="text"
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-amber-400 dark:border-slate-700 dark:bg-slate-900"
                placeholder="例如：夜游，爬山"
                value={avoidTagsDraft}
                onChange={(e) => setAvoidTagsDraft(e.target.value)}
              />
            </label>
            <label className="space-y-1 text-sm sm:col-span-2">
              <span className="text-slate-500">酒店偏好区域</span>
              <input
                type="text"
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-amber-400 dark:border-slate-700 dark:bg-slate-900"
                placeholder="例如：玄武湖 / 新街口"
                value={form.must_have_hotel_area ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, must_have_hotel_area: e.target.value }))}
              />
            </label>
            <label className="space-y-1 text-sm sm:col-span-2">
              <span className="text-slate-500">补充说明</span>
              <textarea
                className="h-20 w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-amber-400 dark:border-slate-700 dark:bg-slate-900"
                placeholder="例如：第一天必须包含玄武湖和钟山风景区，其余时间合理安排。"
                value={form.additional_notes ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, additional_notes: e.target.value }))}
              />
            </label>
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900">
          <div className="mb-3 flex flex-wrap gap-2">
            {PRESET_REQUESTS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => applyPreset(preset.value)}
                className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-700 hover:border-sky-300 hover:bg-sky-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              >
                {preset.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={handleStart}
            disabled={!canStart}
            className="inline-flex h-10 items-center justify-center rounded-lg bg-gradient-to-r from-sky-500 via-cyan-500 to-emerald-500 px-6 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
          >
            开始规划
          </button>
        </section>
      </div>
    </div>
  );
}
