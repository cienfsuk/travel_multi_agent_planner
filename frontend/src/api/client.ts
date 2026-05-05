import type {
  CaseSummary,
  PersonalizationApplyResult,
  PersonalizationResult,
  PlanResponse,
  RoutePlanRequest,
  RoutePlanResponse,
  SSEEvent,
  SystemStatus,
  TripRequest,
} from "../types/api";

const BASE = "/api";

export async function fetchConfig(): Promise<{ tencent_map_js_key: string }> {
  const res = await fetch(`${BASE}/config`);
  if (!res.ok) return { tencent_map_js_key: "" };
  return res.json();
}

export async function fetchStatus(): Promise<SystemStatus> {
  const res = await fetch(`${BASE}/status`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchCases(): Promise<CaseSummary[]> {
  const res = await fetch(`${BASE}/cases`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchLatestCase(): Promise<PlanResponse> {
  const res = await fetch(`${BASE}/cases/latest`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchCase(caseId: string): Promise<PlanResponse> {
  const res = await fetch(`${BASE}/cases/${caseId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchRoutePlan(
  request: RoutePlanRequest,
): Promise<RoutePlanResponse> {
  const res = await fetch(`${BASE}/route/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const raw = await res.text();
    try {
      const parsed = JSON.parse(raw) as { detail?: string };
      throw new Error(parsed.detail || raw);
    } catch {
      throw new Error(raw);
    }
  }
  return res.json();
}

export async function processPersonalizationRequirement(
  userText: string,
): Promise<PersonalizationResult> {
  const res = await fetch(`${BASE}/personalize/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_text: userText }),
  });
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json();
}

export async function applyPersonalizationModification(
  requirementId: string,
  saveExtensions: boolean,
): Promise<PersonalizationApplyResult> {
  const res = await fetch(`${BASE}/personalize/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      requirement_id: requirementId,
      approved: true,
      save_extensions: saveExtensions,
    }),
  });
  if (!res.ok) throw new Error(await readApiError(res));
  return res.json();
}

async function readApiError(res: Response): Promise<string> {
  const raw = await res.text();
  try {
    const parsed = JSON.parse(raw) as { detail?: string };
    return parsed.detail || raw || `HTTP ${res.status}`;
  } catch {
    return raw || `HTTP ${res.status}`;
  }
}

/**
 * Stream a plan via SSE. Calls `onEvent` for each parsed SSE event.
 * Returns an AbortController so the caller can cancel the stream.
 */
export function streamPlan(
  request: TripRequest,
  onEvent: (event: SSEEvent) => void,
  onError?: (err: Error) => void,
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${BASE}/plan/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Parse SSE lines
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";
        for (const chunk of lines) {
          const dataLine = chunk
            .split("\n")
            .find((l) => l.startsWith("data: "));
          if (!dataLine) continue;
          try {
            const event: SSEEvent = JSON.parse(dataLine.slice(6));
            onEvent(event);
          } catch {
            // ignore malformed events
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name !== "AbortError") {
        onError?.(err);
      }
    }
  })();

  return controller;
}
