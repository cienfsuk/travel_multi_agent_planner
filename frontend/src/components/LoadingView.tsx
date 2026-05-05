import { useEffect, useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import type { SSEEvent } from "../types/api";

interface LogEntry {
  agent: string;
  msg: string;
  type?: "warning" | "success" | "default";
}

interface Props {
  events: SSEEvent[];
  error: string | null;
}

export default function LoadingView({ events, error }: Props) {
  const [visibleLogs, setVisibleLogs] = useState<LogEntry[]>([]);
  const [isVisible, setIsVisible] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const prevTraceCountRef = useRef(0);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      setIsVisible(true);
    });

    return () => window.cancelAnimationFrame(frameId);
  }, []);

  // Append only newly-arrived trace events; never reset visibleLogs on re-render
  useEffect(() => {
    const traceEvents = events.filter(
      (e): e is Extract<SSEEvent, { type: "trace" }> => e.type === "trace",
    );
    const newEvents = traceEvents.slice(prevTraceCountRef.current);
    if (newEvents.length === 0) return;
    prevTraceCountRef.current = traceEvents.length;
    const newEntries: LogEntry[] = newEvents.map((ev) => {
      const isWarning = ev.msg.includes("⚠️") || ev.msg.includes("警告");
      const isSuccess = ev.msg.includes("✅") || ev.msg.includes("通过");
      return {
        agent: ev.agent,
        msg: ev.msg,
        type: isWarning ? "warning" : isSuccess ? "success" : "default",
      };
    });
    setVisibleLogs((prev) => [...prev, ...newEntries]);
  }, [events]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [visibleLogs]);

  const isDone = events.some((e) => e.type === "done");
  const hasTraces = events.some((e) => e.type === "trace");

  return (
    <div
      className={`flex min-h-[80vh] flex-col items-center justify-center px-4 transition-all duration-700 ease-out ${
        isVisible
          ? "translate-y-0 scale-100 opacity-100"
          : "translate-y-4 scale-[0.985] opacity-0"
      }`}
    >
      <div className="w-full max-w-3xl bg-slate-900 rounded-2xl shadow-2xl overflow-hidden border border-slate-700 font-mono text-sm">
        {/* Terminal top bar */}
        <div className="bg-slate-800 px-4 py-3 border-b border-slate-700 flex items-center gap-3">
          <div className="flex gap-1.5">
            <div className="w-3 h-3 rounded-full bg-red-500" />
            <div className="w-3 h-3 rounded-full bg-yellow-500" />
            <div className="w-3 h-3 rounded-full bg-green-500" />
          </div>
          <div className="flex items-center gap-2 text-slate-300 font-medium">
            {isDone ? (
              <span className="text-green-400">
                ✅ 规划完成，正在加载结果...
              </span>
            ) : error ? (
              <span className="text-red-400">❌ 规划失败：{error}</span>
            ) : (
              <>
                <Loader2 className="w-4 h-4 animate-spin text-indigo-400" />
                <span>
                  {hasTraces
                    ? "多智能体正在协同处理您的请求..."
                    : "正在连接多智能体系统..."}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Log output */}
        <div className="p-6 h-80 overflow-y-auto space-y-4">
          {visibleLogs.length === 0 && !error && (
            <div className="text-slate-500 animate-pulse">
              等待智能体响应...
            </div>
          )}
          {visibleLogs.map((log, i) => (
            <div key={i} className="animate-fade-in-up">
              <div className="flex items-start gap-3">
                <div
                  className={`shrink-0 mt-0.5 px-2 py-0.5 rounded text-xs font-bold ${
                    log.agent.includes("Coordinator") ||
                    log.agent.includes("主管")
                      ? "bg-purple-500/20 text-purple-400"
                      : log.agent.includes("User") ||
                          log.agent.includes("Requirement")
                        ? "bg-blue-500/20 text-blue-400"
                        : "bg-slate-700 text-slate-300"
                  }`}
                >
                  [{log.agent}]
                </div>
                <div
                  className={`flex-1 ${
                    log.type === "warning"
                      ? "text-yellow-400"
                      : log.type === "success"
                        ? "text-green-400"
                        : "text-slate-300"
                  }`}
                >
                  {log.msg}
                </div>
              </div>
            </div>
          ))}
          <div ref={logsEndRef} />
        </div>
      </div>

      {!error && (
        <p className="mt-6 text-slate-500 text-sm flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-indigo-500 animate-pulse" />
          {isDone
            ? "结果生成完毕，即将跳转..."
            : "高级 AI 正在解决行程冲突与约束..."}
        </p>
      )}
    </div>
  );
}
