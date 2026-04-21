import { useState, useRef, useEffect } from "react";
import { Menu, Sparkles, Sun, Moon } from "lucide-react";
import HomeView from "./components/HomeView";
import LoadingView from "./components/LoadingView";
import ResultView from "./components/ResultView";
import SidebarView from "./components/SidebarView";
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
  const abortRef = useRef<AbortController | null>(null);

  // Initialize theme from localStorage and apply it
  useEffect(() => {
    const savedTheme = localStorage.getItem("theme") as Theme | null;
    const prefersDark = window.matchMedia(
      "(prefers-color-scheme: dark)",
    ).matches;
    const initialTheme = savedTheme || (prefersDark ? "dark" : "light");
    setTheme(initialTheme);
    applyTheme(initialTheme);
  }, []);

  // Apply theme whenever it changes
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

  const applyTheme = (newTheme: Theme) => {
    const htmlElement = document.documentElement;
    if (newTheme === "dark") {
      htmlElement.classList.add("dark");
    } else {
      htmlElement.classList.remove("dark");
    }
    localStorage.setItem("theme", newTheme);
  };

  const toggleTheme = () => {
    const newTheme = theme === "light" ? "dark" : "light";
    setTheme(newTheme);
  };

  const handleStart = (request: TripRequest) => {
    // Cancel any previous stream
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
          // Brief delay so user sees "完成" before transitioning
          setTimeout(() => setAppState("result"), 1000);
        } else if (event.type === "error") {
          setStreamError(event.msg);
        }
      },
      (err) => {
        setStreamError(err.message);
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
    } catch (err) {
      setStreamError(err instanceof Error ? err.message : "加载失败");
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-sans transition-colors relative">
      {/* Navigation */}
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-white/60 bg-white/82 px-6 backdrop-blur transition-colors dark:border-slate-700/80 dark:bg-slate-900/82">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 transition-colors"
            title="历史规划"
          >
            <Menu className="w-5 h-5" />
          </button>
          <button
            onClick={handleReset}
            className="flex items-center gap-2 transition-opacity hover:opacity-80"
          >
            <Sparkles className="h-6 w-6 text-sky-500 dark:text-cyan-400" />
            <span className="bg-gradient-to-r from-sky-500 via-cyan-500 to-emerald-500 bg-clip-text text-xl font-extrabold tracking-tight text-transparent">
              TravelPlanner.ai
            </span>
          </button>
        </div>
        <div className="flex items-center gap-4">
          {appState !== "home" && (
            <button
              className="text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-colors"
              onClick={handleReset}
            >
              重新开始
            </button>
          )}
          <button
            onClick={toggleTheme}
            className="p-2 rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors"
            title={theme === "light" ? "切换至夜间模式" : "切换至白天模式"}
          >
            {theme === "light" ? (
              <Moon className="w-5 h-5" />
            ) : (
              <Sun className="w-5 h-5" />
            )}
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
          className={`min-w-0 flex-1 ${displayedState === "home" ? "w-full" : "mx-auto max-w-[1600px] px-6"} transform-gpu transition-all duration-500 ease-out ${
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
      </main>
    </div>
  );
}
