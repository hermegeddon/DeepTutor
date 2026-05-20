"use client";

import { useParams } from "next/navigation";
import { useEffect, useState, useRef, useCallback } from "react";
import { Lightbulb, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { apiUrl, wsUrl } from "@/lib/api";
import ModuleTree from "@/components/learning/ModuleTree";

interface StreamEvent {
  type: string;
  source: string;
  stage: string;
  content: string;
  metadata: Record<string, unknown>;
  turn_id?: string;
}

interface StageProgress {
  stage: string;
  status: "pending" | "active" | "completed" | "error";
  content: string;
}

export default function LearningBookPage() {
  const params = useParams<{ bookId: string }>();
  const { t } = useTranslation();
  const [stages, setStages] = useState<StageProgress[]>([]);
  const stagesRef = useRef<StageProgress[]>([]);
  const [currentStage, setCurrentStage] = useState<string>("");
  const currentStageRef = useRef<string>("");
  const [connecting, setConnecting] = useState(true);
  const [waitingForLLM, setWaitingForLLM] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorRef = useRef<boolean>(false);
  const activeTurnRetryRef = useRef<boolean>(false);
  const [toast, setToast] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoAdvanceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  interface ModuleData {
    id: string;
    name: string;
    order: number;
    knowledge_points: { id: string; name: string; type: string }[];
    pass_threshold: number;
  }
  const [modules, setModules] = useState<ModuleData[]>([]);
  const [masteryLevels, setMasteryLevels] = useState<Record<string, number>>({});
  const [currentModuleId, setCurrentModuleId] = useState<string>("");
  const [moduleSelected, setModuleSelected] = useState(false);
  const [waitingForInput, setWaitingForInput] = useState(false);
  const [inputPrompt, setInputPrompt] = useState("");
  const [userInput, setUserInput] = useState("");
  const currentTurnRef = useRef<string>("");
  const modulesLengthRef = useRef(0);
  const moduleSelectedRef = useRef(false);
  const currentModuleIdRef = useRef("");

  const handleModuleClick = useCallback((moduleId: string) => {
    if (moduleId === currentModuleId) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: "change_module",
        session_id: params.bookId,
        module_id: moduleId,
      }));
    }
  }, [currentModuleId]);

  const fetchProgress = useCallback(() => {
    fetch(apiUrl(`/api/v1/learning/progress/${params.bookId}`), { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        setModules(data.modules ?? []);
        setMasteryLevels(data.mastery_levels ?? {});
        setCurrentModuleId(data.current_module_id ?? "");
      })
      .catch(() => {});
  }, [params.bookId]);

  const fetchProgressRef = useRef(fetchProgress);
  fetchProgressRef.current = fetchProgress;

  // Keep refs in sync so connect() reads latest values without being recreated.
  modulesLengthRef.current = modules.length;
  moduleSelectedRef.current = moduleSelected;
  currentModuleIdRef.current = currentModuleId;

  // Use a ref for handleStreamEvent so the WebSocket onmessage handler
  // always calls the latest version instead of a stale closure.
  const handleStreamEventRef = useRef<(evt: StreamEvent) => void>(() => {});

  const connect = useCallback(() => {
    // Skip auto-connect when multiple modules exist and none selected yet
    if (modulesLengthRef.current > 1 && !moduleSelectedRef.current && !currentModuleIdRef.current) return;
    // Clear any pending reconnect timer
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    intentionalCloseRef.current = false;

    const ws = new WebSocket(wsUrl("/api/v1/ws"));
    wsRef.current = ws;

    ws.onopen = () => {
      setConnecting(false);
      setError(null);
      reconnectAttemptsRef.current = 0;
      // Check for existing active turn before starting a new one
      ws.send(JSON.stringify({
        type: "check_active_turn",
        session_id: params.bookId,
      }));
    };

    ws.onmessage = (event) => {
      try {
        const evt: StreamEvent = JSON.parse(event.data);
        handleStreamEventRef.current(evt);
      } catch { /* ignore parse errors */ }
    };

    ws.onerror = () => setError(t("guidedLearning.connectionError"));
    ws.onclose = () => {
      if (intentionalCloseRef.current) return;
      setConnecting(true);
      if (reconnectAttemptsRef.current < 5) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 10000);
        reconnectAttemptsRef.current += 1;
        reconnectTimerRef.current = setTimeout(() => connect(), delay);
      } else {
        setError(t("guidedLearning.connectionError"));
        setConnecting(false);
      }
    };
  }, [params.bookId, t]);

  const handleStreamEvent = (evt: StreamEvent) => {
    // Capture turn_id from any event that carries it
    if (evt.turn_id) {
      currentTurnRef.current = evt.turn_id;
    }
    if (evt.type === "wait_for_input") {
      setWaitingForInput(true);
      setInputPrompt(evt.content || "Please enter your answer");
      return;
    }
    if (evt.type === "module_changed") {
      // Cancel any pending auto-advance timer from the previous turn
      if (autoAdvanceTimerRef.current) {
        clearTimeout(autoAdvanceTimerRef.current);
        autoAdvanceTimerRef.current = null;
      }
      const e = evt as unknown as Record<string, unknown>;
      if (e.success !== false) {
        setCurrentModuleId(typeof e.module_id === "string" ? e.module_id : "");
      }
      return;
    }
    if (evt.type === "active_turn_info") {
      const info = evt as unknown as { turn_id: string; status: string };
      if (info.turn_id && info.status === "running") {
        // Resume existing turn
        currentTurnRef.current = info.turn_id;
        wsRef.current?.send(JSON.stringify({
          type: "subscribe_turn",
          turn_id: info.turn_id,
        }));
        setWaitingForLLM(true);
      } else {
        // No active turn — start a new one
        wsRef.current?.send(JSON.stringify({
          type: "start_turn",
          session_id: params.bookId,
          capability: "guided_learning",
          content: "Start learning",
          book_references: [{ book_id: params.bookId, page_ids: [] }],
          config: {},
        }));
        setWaitingForLLM(true);
      }
      return;
    }
    if (evt.type === "stage_start") {
      setWaitingForLLM(false);
      currentStageRef.current = evt.stage;
      setCurrentStage(evt.stage);
      // Clear errors on new stage — previous turn's error should not block the next turn
      errorRef.current = false;
      setError(null);
      activeTurnRetryRef.current = false;
      setStages(prev => {
        const updated = [...prev];
        const idx = updated.findIndex(s => s.stage === evt.stage);
        if (idx >= 0) {
          updated[idx] = { ...updated[idx], status: "active", content: "" };
        } else {
          updated.push({ stage: evt.stage, status: "active", content: "" });
        }
        return updated;
      });
      // Update ref OUTSIDE setStages callback so it is always synchronous.
      // This prevents a race where `done` reads a stale stagesRef before
      // React processes the batched setState.
      {
        const idx = stagesRef.current.findIndex(s => s.stage === evt.stage);
        if (idx >= 0) {
          stagesRef.current = [...stagesRef.current];
          stagesRef.current[idx] = { ...stagesRef.current[idx], status: "active", content: "" };
        } else {
          stagesRef.current = [...stagesRef.current, { stage: evt.stage, status: "active", content: "" }];
        }
      }
    } else if (evt.type === "content") {
      setStages(prev => prev.map(s =>
        s.stage === currentStageRef.current ? { ...s, content: s.content + evt.content } : s
      ));
    } else if (evt.type === "result" || evt.type === "stage_end") {
      const endedStage = currentStageRef.current;
      // Update ref OUTSIDE setStages callback so it is synchronous and
      // visible to the `done` handler that may run in the very next message.
      if (endedStage) {
        stagesRef.current = stagesRef.current.map(s =>
          s.stage === endedStage ? { ...s, status: "completed" as const } : s
        );
      }
      setStages(prev => {
        const updated = prev.map(s =>
          s.stage === endedStage ? { ...s, status: "completed" as const } : s
        );
        return updated;
      });
      currentStageRef.current = "";
      setCurrentStage("");
      if (["plan", "diagnostic_phase1", "diagnostic_phase2", "practice_quiz", "review", "module_test"].includes(endedStage)) {
        fetchProgressRef.current();
      }
    } else if (evt.type === "done") {
      // Auto-advance after turn completes.
      // Skip if: terminal "completed" stage exists, any error stage exists, or errorRef is set.
      const hasCompletedTerminal = stagesRef.current.some(s => s.stage === "completed" && s.status === "completed");
      const hasError = errorRef.current || stagesRef.current.some(s => s.status === "error" || s.stage === "error");
      if (!hasCompletedTerminal && !hasError && wsRef.current?.readyState === WebSocket.OPEN) {
        const sendContinue = (retriesLeft: number) => {
          if (wsRef.current?.readyState !== WebSocket.OPEN) return;
          errorRef.current = false;
          activeTurnRetryRef.current = false;
          wsRef.current.send(JSON.stringify({
            type: "start_turn",
            session_id: params.bookId,
            capability: "guided_learning",
            content: "Continue",
            book_references: [{ book_id: params.bookId, page_ids: [] }],
            config: {},
          }));
          // If still active turn after 2s, retry (only for active turn errors)
          if (retriesLeft > 0) {
            setTimeout(() => {
              if (activeTurnRetryRef.current && retriesLeft > 0) {
                sendContinue(retriesLeft - 1);
              }
            }, 2000);
          }
        };
        autoAdvanceTimerRef.current = setTimeout(() => sendContinue(3), 500);
      }
    } else if (evt.type === "error") {
      setError(evt.content);
      errorRef.current = true;
      // Check if this is a recoverable "active turn" error
      if (evt.content?.includes("active turn")) {
        activeTurnRetryRef.current = true;
      }
      setToast(t("guidedLearning.stageFailed"));
      const stageToError = currentStageRef.current;
      if (stageToError) {
        stagesRef.current = stagesRef.current.map(s =>
          s.stage === stageToError ? { ...s, status: "error" as const } : s
        );
      }
      setStages(prev => {
        const updated = prev.map(s =>
          s.stage === stageToError ? { ...s, status: "error" as const } : s
        );
        return updated;
      });
    }
  };

  // Keep the ref always pointing at the latest handleStreamEvent
  handleStreamEventRef.current = handleStreamEvent;

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(timer);
  }, [toast]);

  // Fetch learning progress for module tree
  useEffect(() => {
    fetchProgress();
  }, [fetchProgress]);

  useEffect(() => {
    connect();
    return () => {
      intentionalCloseRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      wsRef.current?.close();
    };
  }, [connect]);

  // Module picker: show when multiple modules exist and none selected
  if (!connecting && !moduleSelected && modules.length > 1) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <h2 className="text-xl font-bold mb-2">Select a Module</h2>
          <p className="text-sm text-[var(--muted-foreground)] mb-6">Choose a module to start guided learning</p>
          <div className="flex flex-col gap-3">
            {modules.map(m => (
              <button
                key={m.id}
                onClick={() => { handleModuleClick(m.id); setModuleSelected(true); }}
                className="px-6 py-4 rounded-xl border border-[var(--border)] hover:border-[var(--primary)] text-left transition-colors"
              >
                <div className="font-medium">{m.name}</div>
                <div className="text-xs text-[var(--muted-foreground)] mt-1">
                  {m.knowledge_points.length} knowledge points
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full relative">
      {toast && (
        <div className="fixed top-4 right-4 z-50 rounded-lg bg-red-500/90 px-4 py-2 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
      {/* Sidebar */}
      <div className="w-64 border-r border-[var(--border)] p-4 overflow-y-auto flex flex-col gap-4">
        {/* Module tree */}
        {modules.length > 0 && (
          <div>
            <h2 className="text-sm font-semibold mb-2">{t("guidedLearning.modules")}</h2>
            <ModuleTree
              modules={modules}
              masteryLevels={masteryLevels}
              currentModuleId={currentModuleId}
              currentStage={currentStage}
              onModuleClick={handleModuleClick}
            />
          </div>
        )}
        {/* Stage progress */}
        <div>
          <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <Lightbulb className="w-4 h-4 text-[var(--primary)]" />
            {t("guidedLearning.stages")}
          </h2>
        {connecting && <Loader2 className="w-4 h-4 animate-spin text-[var(--muted-foreground)]" />}
        {stages.map((s) => (
          <div key={s.stage} className="flex items-center gap-2 py-1 text-sm">
            <span className={s.status === "completed" ? "text-green-500" : s.status === "error" ? "text-red-500" : "text-[var(--muted-foreground)]"}>
              {s.status === "active" ? <Loader2 className="w-3 h-3 animate-spin" /> :
               s.status === "completed" ? "✓" : s.status === "error" ? "✗" : "○"}
            </span>
            <span className={s.status === "active" ? "text-[var(--foreground)]" : "text-[var(--muted-foreground)]"}>
              {t(`guidedLearning.stage.${s.stage}`, s.stage)}
            </span>
          </div>
        ))}
        </div>
      </div>
      {/* Content area */}
      <div className="flex-1 p-8 overflow-y-auto">
        {error && (
          <div className="text-red-500 mb-4 flex items-center gap-2">
            <span>{error}</span>
            <button
              onClick={() => {
                setError(null);
                reconnectAttemptsRef.current = 0;
                connect();
              }}
              className="px-3 py-1 text-sm rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90"
            >
              {t("guidedLearning.reconnect", "Reconnect")}
            </button>
          </div>
        )}
        {(stages.find(s => s.status === "active") || stages.find(s => s.status === "completed" && s.content))?.content ? (
          <div className="prose dark:prose-invert max-w-none whitespace-pre-wrap">
            {(stages.find(s => s.status === "active") || stages.find(s => s.status === "completed" && s.content))?.content}
          </div>
        ) : (
          !waitingForInput && (
            <div className="flex items-center justify-center h-full text-[var(--muted-foreground)]">
              {connecting ? (
                <Loader2 className="w-8 h-8 animate-spin" />
              ) : waitingForLLM ? (
                <div className="flex flex-col items-center gap-2">
                  <Loader2 className="w-8 h-8 animate-spin" />
                  <p className="text-sm">Generating diagnostic questions...</p>
                </div>
              ) : (
                t("guidedLearning.ready")
              )}
            </div>
          )
        )}
        {waitingForInput && (
          <div className="mt-4 p-4 border border-[var(--border)] rounded-lg">
            <p className="mb-2 text-sm text-[var(--foreground)]">{inputPrompt}</p>
            <textarea
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
              className="w-full p-2 border border-[var(--border)] rounded bg-[var(--background)] text-[var(--foreground)] resize-y min-h-[80px]"
              placeholder={t("guidedLearning.inputPlaceholder", "Type your answer...")}
            />
            <button
              onClick={() => {
                if (!currentTurnRef.current) return;
                wsRef.current?.send(JSON.stringify({
                  type: "user_input",
                  turn_id: currentTurnRef.current,
                  content: userInput,
                }));
                setWaitingForInput(false);
                setUserInput("");
              }}
              className="mt-2 px-4 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] text-sm hover:opacity-90"
            >
              {t("guidedLearning.submit", "Submit")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
