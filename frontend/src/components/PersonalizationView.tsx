import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  FileCode,
  Loader2,
  Send,
  Shield,
  Sparkles,
  X,
} from "lucide-react";
import {
  translateModificationType,
  translatePatchOperation,
  translateRiskLevel,
} from "../utils/translations";
import {
  applyPersonalizationModification,
  processPersonalizationRequirement,
} from "../api/client";
import type { PersonalizationResult } from "../types/api";

interface PersonalizationViewProps {
  onClose: () => void;
  onApplied?: (appliedRequirement: string) => void | Promise<void>;
}

const SOURCE_LABELS: Record<string, string> = {
  llm: "原始大模型生成",
  template: "规则生成",
  repaired: "修复后生成",
};

const STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  warning: "需注意",
  blocked: "阻塞",
  failed: "失败",
  pending_approval: "待确认",
  pending_review: "待人工复核",
  applied: "已应用",
  error: "出错",
};

export default function PersonalizationView({
  onClose,
  onApplied,
}: PersonalizationViewProps) {
  const [userInput, setUserInput] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);
  const [result, setResult] = useState<PersonalizationResult | null>(null);
  const [isApplying, setIsApplying] = useState(false);
  const [applied, setApplied] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [expandedPatches, setExpandedPatches] = useState<Set<number>>(new Set());
  const [saveExtensions, setSaveExtensions] = useState(true);

  const patchCount = result?.modification_patch?.patches.length ?? 0;
  const hasBlockingIssues = (result?.blocking_issues?.length ?? 0) > 0;
  const subRequirements = result?.sub_requirements ?? [];
  const agentTrace = result?.agent_trace ?? [];
  const validation = result?.explanation?.validation;

  const summaryText = useMemo(() => {
    return result?.explanation?.summary || result?.error_message || "";
  }, [result]);

  const handleSubmit = async () => {
    if (!userInput.trim()) return;

    setIsProcessing(true);
    setResult(null);
    setApplied(false);
    setErrorMessage(null);

    try {
      const data = await processPersonalizationRequirement(userInput);
      setResult(data);
      if (data.error_message) {
        setErrorMessage(data.error_message);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "个性化需求分析失败");
    } finally {
      setIsProcessing(false);
    }
  };

  const handleApply = async () => {
    if (!result) return;

    setIsApplying(true);
    setErrorMessage(null);

    try {
      const data = await applyPersonalizationModification(
        result.requirement_id,
        saveExtensions,
      );
      if (!data.success) {
        throw new Error(data.apply_message || "应用个性化修改失败");
      }

      setApplied(true);
      await onApplied?.(result.raw_requirement || userInput);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "应用个性化修改失败");
    } finally {
      setIsApplying(false);
    }
  };

  const togglePatch = (index: number) => {
    setExpandedPatches((current) => {
      const next = new Set(current);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const riskClass = (level: string) => {
    switch (level) {
      case "high":
        return "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300";
      case "medium":
        return "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300";
      default:
        return "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300";
    }
  };

  const statusBadgeClass = (status: string) => {
    switch (status) {
      case "blocked":
      case "failed":
      case "error":
        return "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300";
      case "warning":
      case "pending_review":
        return "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";
      case "applied":
      case "ok":
      case "pending_approval":
        return "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300";
      default:
        return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-200 bg-gradient-to-r from-sky-50 to-cyan-50 px-6 py-4 dark:border-slate-800 dark:from-slate-900 dark:to-slate-900">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-sky-500 to-cyan-500">
              <Sparkles className="h-5 w-5 text-white" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-slate-900 dark:text-white">
                个性化定制
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                先生成可审阅方案，再确认是否应用到运行时智能体。
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto p-6">
          <div className="space-y-3">
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300">
              输入你的个性化需求
            </label>
            <textarea
              value={userInput}
              onChange={(event) => setUserInput(event.target.value)}
              placeholder="例如：第二天别太早出发、晚饭想吃火锅、行程轻松一点、酒店靠近地铁。"
              className="h-28 w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sky-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
            />
            <button
              onClick={handleSubmit}
              disabled={!userInput.trim() || isProcessing}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-sky-500 to-cyan-500 px-5 py-2.5 text-sm font-medium text-white shadow-lg shadow-sky-500/25 transition-all hover:from-sky-600 hover:to-cyan-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isProcessing ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  分析中
                </>
              ) : (
                <>
                  <Send className="h-4 w-4" />
                  生成修改方案
                </>
              )}
            </button>
          </div>

          {errorMessage && (
            <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
              {errorMessage}
            </div>
          )}

          {result && (
            <>
              <div className="grid gap-4 md:grid-cols-3">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-800/60 md:col-span-2">
                  <div className="mb-2 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                    <FileCode className="h-3.5 w-3.5" />
                    当前需求
                  </div>
                  <p className="text-sm font-medium text-slate-900 dark:text-white">
                    {result.raw_requirement || userInput}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-full bg-sky-100 px-2.5 py-1 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
                      {translateModificationType(result.modification_type)}
                    </span>
                    <span
                      className={`rounded-full px-2.5 py-1 ${statusBadgeClass(
                        result.status || "pending_approval",
                      )}`}
                    >
                      {STATUS_LABELS[result.status || "pending_approval"] ||
                        result.status}
                    </span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                      目标文件 {result.target_files?.length ?? 0} 个
                    </span>
                  </div>
                </div>

                <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-800/50">
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    生成概览
                  </div>
                  <div className="mt-3 space-y-2 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">
                        总尝试次数
                      </span>
                      <span className="font-medium text-slate-900 dark:text-white">
                        {result.attempt_count ?? 0}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">
                        修复重试
                      </span>
                      <span className="font-medium text-slate-900 dark:text-white">
                        {result.repair_attempts ?? 0}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">
                        最终来源
                      </span>
                      <span className="font-medium text-slate-900 dark:text-white">
                        {SOURCE_LABELS[result.final_generation_source || "template"] ||
                          result.final_generation_source}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">
                        代码文件
                      </span>
                      <span className="font-medium text-slate-900 dark:text-white">
                        {patchCount}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {summaryText && (
                <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-700 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-300">
                  {summaryText}
                </div>
              )}

              {result.impact_report && (
                <div
                  className={`rounded-xl border p-4 ${riskClass(
                    result.impact_report.risk_level,
                  )}`}
                >
                  <div className="mb-3 flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4" />
                    <span className="text-sm font-semibold">影响分析</span>
                  </div>
                  <div className="grid gap-3 text-sm md:grid-cols-3">
                    <div>
                      <div className="text-xs opacity-80">风险等级</div>
                      <div className="mt-1 font-medium">
                        {translateRiskLevel(result.impact_report.risk_level)}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs opacity-80">影响文件</div>
                      <div className="mt-1 font-medium">
                        {result.impact_report.impacted_files.length} 个
                      </div>
                    </div>
                    <div>
                      <div className="text-xs opacity-80">影响智能体</div>
                      <div className="mt-1 font-medium">
                        {result.impact_report.impacted_agents.join("、") || "无"}
                      </div>
                    </div>
                  </div>
                  {result.impact_report.summary && (
                    <p className="mt-3 text-xs opacity-80">
                      {result.impact_report.summary}
                    </p>
                  )}
                </div>
              )}

              {hasBlockingIssues && (
                <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/30">
                  <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-red-700 dark:text-red-300">
                    <AlertTriangle className="h-4 w-4" />
                    存在阻塞问题，当前不能直接应用
                  </div>
                  <div className="space-y-2 text-xs text-red-700 dark:text-red-300">
                    {result.blocking_issues?.map((issue, index) => (
                      <div key={index} className="rounded-lg bg-white/60 p-2 dark:bg-black/10">
                        {issue}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid gap-4 lg:grid-cols-2">
                <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-800/50">
                  <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-white">
                    智能体执行链路
                  </h3>
                  <div className="space-y-3">
                    {agentTrace.length === 0 && (
                      <div className="rounded-lg border border-dashed border-slate-300 p-3 text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400">
                        当前没有可展示的执行轨迹。
                      </div>
                    )}
                    {agentTrace.map((item, index) => (
                      <div
                        key={`${item.stage}-${index}`}
                        className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div>
                            <div className="text-sm font-medium text-slate-900 dark:text-white">
                              {item.agent}
                            </div>
                            <div className="text-xs text-slate-500 dark:text-slate-400">
                              阶段：{item.stage}
                            </div>
                          </div>
                          <span
                            className={`rounded-full px-2 py-0.5 text-xs ${statusBadgeClass(
                              item.status,
                            )}`}
                          >
                            {STATUS_LABELS[item.status] || item.status}
                          </span>
                        </div>
                        <div className="mt-2 text-sm text-slate-700 dark:text-slate-300">
                          {item.summary}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>

                <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-800/50">
                  <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-white">
                    子需求与修复情况
                  </h3>
                  <div className="space-y-3">
                    {subRequirements.length === 0 && (
                      <div className="rounded-lg border border-dashed border-slate-300 p-3 text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400">
                        当前没有子需求信息。
                      </div>
                    )}
                    {subRequirements.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="text-sm font-medium text-slate-900 dark:text-white">
                              {item.text}
                            </div>
                            <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                              {item.target_agent}.{item.target_method}
                            </div>
                          </div>
                          <span
                            className={`rounded-full px-2 py-0.5 text-xs ${statusBadgeClass(
                              item.blocking_issues.length ? "blocked" : "ok",
                            )}`}
                          >
                            {item.blocking_issues.length ? "阻塞" : "可用"}
                          </span>
                        </div>
                        <div className="mt-3 grid gap-2 text-xs text-slate-600 dark:text-slate-300 md:grid-cols-2">
                          <div>生成来源：{SOURCE_LABELS[item.generation_source] || item.generation_source}</div>
                          <div>尝试次数：{item.attempt_count}</div>
                          <div>修复次数：{item.repair_attempts}</div>
                          <div>签名校验：{item.runtime_signature_ok ? "通过" : "失败"}</div>
                          <div>审查结果：{item.review_passed ? "通过" : "未通过"}</div>
                          <div>验证结果：{item.validation_success ? "通过" : "未通过"}</div>
                        </div>
                        {item.blocking_issues.length > 0 && (
                          <div className="mt-2 space-y-1 text-xs text-red-600 dark:text-red-300">
                            {item.blocking_issues.map((issue, index) => (
                              <div key={index}>• {issue}</div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              </div>

              {result.review_result && (
                <div
                  className={`rounded-xl border p-4 ${
                    result.review_result.passed
                      ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20"
                      : "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20"
                  }`}
                >
                  <div className="mb-3 flex items-center gap-2">
                    <Shield
                      className={`h-4 w-4 ${
                        result.review_result.passed
                          ? "text-emerald-500"
                          : "text-red-500"
                      }`}
                    />
                    <span className="text-sm font-semibold text-slate-900 dark:text-white">
                      代码审查
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs text-slate-600 dark:text-slate-300">
                    <span>结论：{result.review_result.passed ? "通过" : "未通过"}</span>
                    <span>建议：{result.review_result.recommendation}</span>
                    <span>
                      是否启用大模型审查：
                      {result.review_result.llm_review_used ? "是" : "否"}
                    </span>
                  </div>
                  {result.review_result.issues.length > 0 && (
                    <div className="mt-3 space-y-2">
                      {result.review_result.issues.map((issue, index) => (
                        <div
                          key={index}
                          className="rounded-lg bg-white/70 p-3 text-xs text-slate-700 dark:bg-black/10 dark:text-slate-300"
                        >
                          <div className="font-medium">
                            [{issue.severity}] {issue.category}
                          </div>
                          <div className="mt-1">{issue.message}</div>
                          {issue.suggestion && (
                            <div className="mt-1 text-slate-500 dark:text-slate-400">
                              建议：{issue.suggestion}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {validation && (
                <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-800/50">
                  <div className="mb-2 text-sm font-semibold text-slate-900 dark:text-white">
                    校验结果
                  </div>
                  <div className="grid gap-2 text-xs text-slate-600 dark:text-slate-300 md:grid-cols-3">
                    <div>总体结果：{validation.success ? "通过" : "失败"}</div>
                    <div>签名检查：{validation.runtime_signature_ok ? "通过" : "失败"}</div>
                    <div>冒烟检查：{validation.smoke_checks?.length ?? 0}</div>
                  </div>
                  {validation.message && (
                    <div className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                      {validation.message}
                    </div>
                  )}
                  {validation.tests_failed?.length > 0 && (
                    <div className="mt-3 space-y-1 text-xs text-red-600 dark:text-red-300">
                      {validation.tests_failed.map((item, index) => (
                        <div key={index}>• {item}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {patchCount > 0 && (
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold text-slate-900 dark:text-white">
                    修改详情
                  </h4>
                  {result.modification_patch?.patches.map((patch, index) => (
                    <div
                      key={index}
                      className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700"
                    >
                      <button
                        onClick={() => togglePatch(index)}
                        className="flex w-full items-center justify-between bg-slate-50 px-4 py-3 text-left transition-colors hover:bg-slate-100 dark:bg-slate-800 dark:hover:bg-slate-700"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium text-slate-900 dark:text-white">
                            {patch.file_path}
                          </div>
                          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                            {translatePatchOperation(patch.operation)}
                          </div>
                        </div>
                        {expandedPatches.has(index) ? (
                          <ChevronUp className="h-4 w-4 text-slate-400" />
                        ) : (
                          <ChevronDown className="h-4 w-4 text-slate-400" />
                        )}
                      </button>
                      {expandedPatches.has(index) && patch.new_snippet && (
                        <div className="rounded-b-xl bg-slate-950 px-4 py-3">
                          <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs text-slate-200">
                            {patch.new_snippet}
                          </pre>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {result.requires_confirmation && !applied && (
                <div className="flex items-center gap-3 border-t border-slate-200 pt-4 dark:border-slate-800">
                  <label className="group flex cursor-pointer items-center gap-2">
                    <div
                      className={`relative h-6 w-11 rounded-full transition-colors ${
                        saveExtensions
                          ? "bg-emerald-500"
                          : "bg-slate-300 dark:bg-slate-700"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={saveExtensions}
                        onChange={(event) =>
                          setSaveExtensions(event.target.checked)
                        }
                        className="sr-only"
                      />
                      <div
                        className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
                          saveExtensions ? "translate-x-5" : ""
                        }`}
                      />
                    </div>
                    <span className="text-sm text-slate-600 transition-colors group-hover:text-slate-800 dark:text-slate-400 dark:group-hover:text-white">
                      保存到本地，下次启动继续生效
                    </span>
                  </label>
                </div>
              )}

              {result.requires_confirmation && !applied && (
                <div className="flex items-center justify-end gap-3 border-t border-slate-200 pt-4 dark:border-slate-800">
                  <button
                    onClick={onClose}
                    className="px-5 py-2.5 text-sm font-medium text-slate-600 transition-colors hover:text-slate-800 dark:text-slate-400 dark:hover:text-white"
                  >
                    取消
                  </button>
                  <button
                    onClick={handleApply}
                    disabled={isApplying || hasBlockingIssues}
                    className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-emerald-500 to-cyan-500 px-5 py-2.5 text-sm font-medium text-white shadow-lg shadow-emerald-500/25 transition-all hover:from-emerald-600 hover:to-cyan-600 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isApplying ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        应用中
                      </>
                    ) : (
                      <>
                        <Check className="h-4 w-4" />
                        确认执行修改
                      </>
                    )}
                  </button>
                </div>
              )}

              {applied && (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/20">
                  <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
                    <Check className="h-5 w-5" />
                    <span className="font-medium">个性化修改已应用</span>
                  </div>
                  <p className="mt-1 text-xs text-emerald-700/80 dark:text-emerald-300/80">
                    当前规划会按新的个性化规则重新生成。
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
