import { useState, useRef, useEffect } from "react";
import { Menu, Sparkles, Sun, Moon, Wand2 } from "lucide-react";
import HomeView from "./components/HomeView";
import LoadingView from "./components/LoadingView";
import ResultView from "./components/ResultView";
import SidebarView from "./components/SidebarView";
import PersonalizationView from "./components/PersonalizationView";
import { fetchCase, streamPlan } from "./api/client";
import type { PlanResponse, SSEEvent, TripRequest } from "./types/api";

type AppState = "home" | "loading" | "result";
type Theme = "light" | "dark";

export default function App() {
  const [appState, setAppState] = useState<AppState>("home");
  const [displayedState, setDisplayedState] = useState<AppState>("home");
  const [isViewVisible, setIsViewVisible] = useState(true);
  const [sseEvents, setSseEvents] = useState<SSEEvent[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [result, setResult] = useState<PlanResponse | null>(null);
  const [theme, setTheme] = useState<Theme>("light");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [showPersonalization, setShowPersonalization] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const savedTheme = localStorage.getItem("theme") as Theme | null;
    const prefersDark = window.matchMedia(
      "(prefers-color-scheme: dark)",
    ).matches;
    const initialTheme = savedTheme || (prefersDark ? "dark" : "light");
    setTheme(initialTheme);
    applyTheme(initialTheme);
  }, []);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (appState === displayedState) {
      return;
    }

    setIsViewVisible(false);

    const switchTimer = window.setTimeout(() => {
      setDisplayedState(appState);

      window.requestAnimationFrame(() => {
        setIsViewVisible(true);
      });
    }, 180);

    return () => window.clearTimeout(switchTimer);
  }, [appState, displayedState]);

  const applyTheme = (nextTheme: Theme) => {
    const htmlElement = document.documentElement;
    if (nextTheme === "dark") {
      htmlElement.classList.add("dark");
    } else {
      htmlElement.classList.remove("dark");
    }
    localStorage.setItem("theme", nextTheme);
  };

  const toggleTheme = () => {
    setTheme((current) => (current === "light" ? "dark" : "light"));
  };

  const handleStart = (request: TripRequest) => {
    abortRef.current?.abort();
    setSseEvents([]);
    setStreamError(null);
    setResult(null);
    setSelectedCaseId(null);
    setAppState("loading");

    abortRef.current = streamPlan(
      request,
      (event) => {
        setSseEvents((prev) => [...prev, event]);

        if (event.type === "done") {
          setSelectedCaseId(event.case_id);
          setResult({
            case_id: event.case_id,
            plan: event.plan,
            animation: event.animation,
          });
          setTimeout(() => setAppState("result"), 1000);
        } else if (event.type === "error") {
          setStreamError(event.msg);
        }
      },
      (error) => {
        setStreamError(error.message);
      },
    );
  };

  const handleReset = () => {
    abortRef.current?.abort();
    setSseEvents([]);
    setStreamError(null);
    setResult(null);
    setSelectedCaseId(null);
    setAppState("home");
  };

  const handleSelectCase = async (caseId: string) => {
    try {
      setSelectedCaseId(caseId);
      setStreamError(null);
      setAppState("loading");
      setSseEvents([
        {
          type: "trace",
          agent: "系统",
          msg: `正在加载历史规划 ${caseId}...`,
          decisions: [],
        },
      ]);

      const data = await fetchCase(caseId);
      setResult(data);
      setSseEvents((prev) => [
        ...prev,
        { type: "trace", agent: "系统", msg: "加载完成", decisions: [] },
      ]);
      setTimeout(() => setAppState("result"), 600);
    } catch (error) {
      setStreamError(error instanceof Error ? error.message : "加载失败");
    }
  };

  const mergeAdditionalNotes = (
    request: TripRequest,
    personalizationText: string,
  ): TripRequest => {
    const incoming = personalizationText.trim();
    if (!incoming) {
      return request;
    }
    const existing = (request.additional_notes || "").trim();
    if (existing.includes(incoming)) {
      return request;
    }
    return {
      ...request,
      additional_notes: existing ? `${existing}；${incoming}` : incoming,
    };
  };

  return (
    <div className="relative min-h-screen bg-slate-50 font-sans text-slate-800 transition-colors dark:bg-slate-900 dark:text-slate-100">
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-white/60 bg-white/82 px-6 backdrop-blur transition-colors dark:border-slate-700/80 dark:bg-slate-900/82">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setSidebarOpen((value) => !value)}
            className="rounded-lg p-2 text-slate-600 transition-colors hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700"
            title="历史规划"
          >
            <Menu className="h-5 w-5" />
          </button>
          <button
            onClick={handleReset}
            className="flex items-center gap-2 transition-opacity hover:opacity-80"
          >
            <Sparkles className="h-6 w-6 text-sky-500 dark:text-cyan-400" />
            <span className="bg-gradient-to-r from-sky-500 via-cyan-500 to-emerald-500 bg-clip-text text-xl font-extrabold tracking-tight text-transparent">
              游策 AI
            </span>
          </button>
        </div>

        <div className="flex items-center gap-4">
          {appState !== "home" && (
            <button
              className="text-sm font-medium text-slate-500 transition-colors hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
              onClick={handleReset}
            >
              重新开始
            </button>
          )}

          <button
            onClick={toggleTheme}
            className="rounded-lg bg-slate-100 p-2 text-slate-600 transition-colors hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600"
            title={theme === "light" ? "切换到夜间模式" : "切换到白天模式"}
          >
            {theme === "light" ? (
              <Moon className="h-5 w-5" />
            ) : (
              <Sun className="h-5 w-5" />
            )}
          </button>

          <button
            onClick={() => setShowPersonalization(true)}
            className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-sky-500 to-cyan-500 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-sky-500/25 transition-all hover:from-sky-600 hover:to-cyan-600"
            title="个性化定制"
          >
            <Wand2 className="h-4 w-4" />
            <span>定制</span>
          </button>
        </div>
      </header>

      <main className="flex min-h-[calc(100svh-64px)]">
        <SidebarView
          open={sidebarOpen}
          onSelectCase={handleSelectCase}
          selectedCaseId={selectedCaseId}
        />

        <div
          className={`min-w-0 flex-1 ${
            displayedState === "home" ? "w-full" : "mx-auto max-w-[1600px] px-6"
          } transform-gpu transition-all duration-500 ease-out ${
            isViewVisible
              ? "translate-y-0 scale-100 opacity-100"
              : "translate-y-3 scale-[0.985] opacity-0"
          }`}
        >
          {displayedState === "home" && (
            <HomeView onStart={handleStart} theme={theme} />
          )}
          {displayedState === "loading" && (
            <LoadingView events={sseEvents} error={streamError} />
          )}
          {displayedState === "result" && result && (
            <ResultView result={result} />
          )}
        </div>

        {showPersonalization && (
          <PersonalizationView
            onClose={() => setShowPersonalization(false)}
            onApplied={async (appliedRequirement) => {
              const currentRequest = result?.plan.request;
              setShowPersonalization(false);
              if (currentRequest) {
                handleStart(
                  mergeAdditionalNotes(currentRequest, appliedRequirement),
                );
              }
            }}
          />
        )}
      </main>
    </div>
  );
}
