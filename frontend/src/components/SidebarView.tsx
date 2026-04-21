import { useEffect, useState } from "react";
import {
  CalendarDays,
  CheckCircle2,
  Loader2,
  MapPin,
  Server,
  XCircle,
} from "lucide-react";
import { fetchCases, fetchStatus } from "../api/client";
import type { CaseSummary, ProviderStatus } from "../types/api";

interface Props {
  open: boolean;
  onSelectCase: (caseId: string) => void;
  selectedCaseId?: string | null;
}

export default function SidebarView({
  open,
  onSelectCase,
  selectedCaseId,
}: Props) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    Promise.all([fetchStatus(), fetchCases()])
      .then(([statusData, caseData]) => {
        setProviders(statusData.providers ?? []);
        setCases(caseData);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [open]);

  // Parse case_id like "杭州-上海-2d-1000-20260416-165012"
  function parseCaseId(caseId: string) {
    const parts = caseId.split("-");
    if (parts.length >= 4) {
      const origin = parts[0];
      const destination = parts[1];
      const days = parts[2];
      const budget = parts[3];
      return { origin, destination, days, budget };
    }
    return null;
  }

  return (
    <aside
      className={`shrink-0 overflow-hidden border-r border-slate-200 bg-white/92 transition-[width,opacity] duration-300 dark:border-slate-700 dark:bg-slate-900/90 ${
        open ? "w-72 opacity-100" : "w-0 opacity-0"
      }`}
      aria-hidden={!open}
    >
      <div className="h-[calc(100svh-64px)] overflow-y-auto p-3.5">
        <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-800/60">
          <div className="mb-2 flex items-center gap-2">
            <Server className="h-4 w-4 text-sky-500" />
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              Provider 状态
            </h2>
          </div>
          <div className="space-y-2">
            {providers.map((provider) => (
              <div
                key={provider.name}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-900"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-slate-700 dark:text-slate-200">
                    {provider.name}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1 ${provider.active ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}
                  >
                    {provider.active ? (
                      <CheckCircle2 className="h-3.5 w-3.5" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5" />
                    )}
                    {provider.active ? "可用" : "缺失"}
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-4 text-slate-500 dark:text-slate-400">
                  {provider.detail}
                </p>
              </div>
            ))}
            {!loading && !error && providers.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-300 px-2.5 py-2 text-xs text-slate-500 dark:border-slate-600 dark:text-slate-400">
                暂无 Provider 状态数据
              </div>
            )}
          </div>
        </div>

        <div>
          <div className="mb-2 flex items-center gap-2 px-1">
            <MapPin className="h-4 w-4 text-indigo-500" />
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              历史规划记录
            </h2>
          </div>

          <div className="space-y-2">
            {loading && (
              <div className="flex items-center justify-center py-12 text-slate-400">
                <Loader2 className="w-6 h-6 animate-spin" />
                <span className="ml-2 text-sm">加载中...</span>
              </div>
            )}

            {error && (
              <div className="text-sm text-red-500 bg-red-50 dark:bg-red-900/20 rounded-lg p-3 border border-red-200 dark:border-red-800">
                加载失败：{error}
              </div>
            )}

            {!loading && !error && cases.length === 0 && (
              <div className="text-center py-12 text-slate-400 dark:text-slate-500">
                <MapPin className="w-10 h-10 mx-auto mb-3 opacity-40" />
                <p className="text-sm">还没有历史规划</p>
                <p className="text-xs mt-1">生成一次行程后会在这里展示</p>
              </div>
            )}

            {cases.map((c) => {
              const parsed = parseCaseId(c.case_id);
              const isActive = selectedCaseId === c.case_id;
              return (
                <button
                  key={c.case_id}
                  onClick={() => onSelectCase(c.case_id)}
                  className={`w-full rounded-xl border p-3 text-left transition-all group ${
                    isActive
                      ? "border-indigo-400 bg-indigo-50/80 shadow-md ring-1 ring-indigo-300/70 dark:border-indigo-500 dark:bg-indigo-900/30 dark:ring-indigo-500/40"
                      : "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 hover:border-indigo-300 dark:hover:border-indigo-500 hover:shadow-md"
                  }`}
                >
                  <div
                    className={`text-sm font-semibold leading-5 transition-colors ${
                      isActive
                        ? "text-indigo-700 dark:text-indigo-300"
                        : "text-slate-800 dark:text-slate-100 group-hover:text-indigo-600 dark:group-hover:text-indigo-400"
                    }`}
                  >
                    {c.summary || c.case_id}
                  </div>
                  {parsed && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400">
                      <span className="flex items-center gap-1">
                        <MapPin className="w-3 h-3" />
                        {parsed.origin} → {parsed.destination}
                      </span>
                      <span>{parsed.days}</span>
                      <span>¥{parsed.budget}</span>
                    </div>
                  )}
                  {c.generated_at && (
                    <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500">
                      <CalendarDays className="w-3 h-3" />
                      {c.generated_at}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </aside>
  );
}
