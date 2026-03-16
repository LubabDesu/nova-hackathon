import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import ScaffoldReviewCard from "../components/ScaffoldReviewCard";
import AgentActionFeed from "../components/AgentActionFeed";
import AgentQuestionCard from "../components/AgentQuestionCard";
import { cancelPlanningRequest, reviseScaffold, extractIdeaStream } from "../services/api";
import type { AgentActionEvent, AgentQuestionEvent, ItineraryNode } from "../types";
import "../styles/sky-theme.css";
import "../styles/agent-animations.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

// Activity type → accent color + icon
function getActivityStyle(activityType: string): { color: string; icon: string } {
    const t = activityType.toLowerCase();
    if (t.includes("food") || t.includes("restaurant") || t.includes("cuisine") || t.includes("dining")) {
        return { color: "#d97706", icon: "🍜" };
    }
    if (t.includes("temple") || t.includes("shrine") || t.includes("museum") || t.includes("cultural") || t.includes("art")) {
        return { color: "#6366f1", icon: "🏛️" };
    }
    if (t.includes("outdoor") || t.includes("nature") || t.includes("hike") || t.includes("bamboo") || t.includes("park")) {
        return { color: "#16a34a", icon: "🌿" };
    }
    if (t.includes("transfer") || t.includes("transit") || t.includes("train") || t.includes("transport")) {
        return { color: "#64748b", icon: "🚆" };
    }
    if (t.includes("rest") || t.includes("hotel") || t.includes("accommodation") || t.includes("sleep")) {
        return { color: "#7c3aed", icon: "🛏️" };
    }
    if (t.includes("matcha") || t.includes("tea") || t.includes("market") || t.includes("shopping")) {
        return { color: "#ca8a04", icon: "🍵" };
    }
    return { color: "#4a8dc4", icon: "✦" };
}

function parseSseFrame(frame: string): { event: string; data: Record<string, unknown> } | null {
    const lines = frame.split("\n");
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
        if (line.startsWith("event:")) {
            eventName = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).trimStart());
        }
    }
    if (dataLines.length === 0) return null;
    try {
        return { event: eventName, data: JSON.parse(dataLines.join("\n")) as Record<string, unknown> };
    } catch {
        return null;
    }
}

function asItineraryNodeArray(value: unknown): ItineraryNode[] {
    if (!Array.isArray(value)) return [];
    const nodes: ItineraryNode[] = [];
    for (const item of value) {
        if (!item || typeof item !== "object") continue;
        const r = item as Record<string, unknown>;
        if (typeof r.title !== "string" || typeof r.activity_type !== "string") continue;
        nodes.push({
            title: r.title,
            activity_type: r.activity_type,
            duration_mins: typeof r.duration_mins === "number" ? r.duration_mins : null,
            date_local: typeof r.date_local === "string" ? r.date_local : null,
            start_time_local: typeof r.start_time_local === "string" ? r.start_time_local : null,
            end_time_local: typeof r.end_time_local === "string" ? r.end_time_local : null,
            lat: typeof r.lat === "number" ? r.lat : null,
            long: typeof r.long === "number" ? r.long : null,
            description: typeof r.description === "string" ? r.description : null,
            for_travelers: Array.isArray(r.for_travelers)
                ? (r.for_travelers as string[]).filter((x) => typeof x === "string")
                : undefined,
        });
    }
    return nodes;
}

// Group nodes by date
function groupByDate(nodes: ItineraryNode[]): { date: string; nodes: ItineraryNode[] }[] {
    const map = new Map<string, ItineraryNode[]>();
    for (const node of nodes) {
        const key = node.date_local ?? "Unscheduled";
        const arr = map.get(key) ?? [];
        arr.push(node);
        map.set(key, arr);
    }
    return Array.from(map.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([date, nodes]) => ({ date, nodes }));
}

function formatDate(dateStr: string): string {
    if (dateStr === "Unscheduled") return "Unscheduled";
    try {
        return new Date(dateStr + "T12:00:00").toLocaleDateString("en-US", {
            weekday: "long",
            month: "long",
            day: "numeric",
        });
    } catch {
        return dateStr;
    }
}

function formatTime(t: string | null): string {
    if (!t) return "";
    try {
        const [h, m] = t.split(":").map(Number);
        const ampm = h >= 12 ? "PM" : "AM";
        const hour = h % 12 || 12;
        return `${hour}:${String(m).padStart(2, "0")} ${ampm}`;
    } catch {
        return t;
    }
}

export default function GroupPlanPage() {
    const { groupId } = useParams<{ groupId: string }>();
    const { session } = useAuth();
    const navigate = useNavigate();

    // Planning stage tracking
    const [planStages, setPlanStages] = useState<Array<{
        key: string; label: string; status: "pending" | "running" | "done";
    }>>([]);
    const [planError, setPlanError] = useState<string | null>(null);
    const [acceptedCount, setAcceptedCount] = useState<number | null>(null);

    // Agent feed
    const [agentActions, setAgentActions] = useState<AgentActionEvent[]>([]);
    const [agentScratchpad, setAgentScratchpad] = useState("");
    const [agentActive, setAgentActive] = useState(false);
    const planRequestIdRef = useRef<string | null>(null);
    const wasCancelledRef = useRef(false);
    const abortControllerRef = useRef<AbortController | null>(null);

    // Scaffold review
    const [scaffoldReady, setScaffoldReady] = useState(false);
    const [scaffoldText, setScaffoldText] = useState<string | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [revisionCount, setRevisionCount] = useState(0);
    const [maxRevisions, setMaxRevisions] = useState(1);
    const [scaffoldFeedback, setScaffoldFeedback] = useState("");
    const [revisingScaffold, setRevisingScaffold] = useState(false);

    // Extraction + final itinerary
    const [extracting, setExtracting] = useState(false);
    const [nodes, setNodes] = useState<ItineraryNode[]>([]);
    const [extractError, setExtractError] = useState<string | null>(null);
    const [done, setDone] = useState(false);
    const nodeQueueRef = useRef<ItineraryNode[]>([]);
    const drainTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

    // Per-traveler extraction progress
    const [extractedTravelers, setExtractedTravelers] = useState<string[]>([]);
    const [allTravelerNames, setAllTravelerNames] = useState<string[]>([]);

    // Question handling
    const [pendingQuestion, setPendingQuestion] = useState<AgentQuestionEvent | null>(null);
    const [submittingAnswer, setSubmittingAnswer] = useState(false);

    const [planning, setPlanning] = useState(true);

    // Part 4: check if plan already exists on mount — navigate to TripPage
    useEffect(() => {
        if (!groupId || !session?.access_token) return;
        let cancelled = false;
        (async () => {
            try {
                const res = await fetch(`${API_BASE}/trips/${groupId}/nodes`, {
                    headers: { Authorization: `Bearer ${session.access_token}` },
                });
                if (!res.ok || cancelled) return;
                const existing = await res.json();
                if (Array.isArray(existing) && existing.length > 0 && !cancelled) {
                    navigate(`/trips/${groupId}`);
                }
            } catch {
                /* not yet planned, proceed */
            }
        })();
        return () => { cancelled = true; };
    }, [groupId, session?.access_token]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleAnswer = async (answer: string) => {
        if (!pendingQuestion || !groupId) return;
        setSubmittingAnswer(true);
        try {
            const token = session?.access_token;
            await fetch(`${API_BASE}/group-trips/answer-question`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                },
                body: JSON.stringify({
                    group_id: groupId,
                    question_id: pendingQuestion.question_id,
                    answer,
                }),
            });
            setPendingQuestion(null);
        } finally {
            setSubmittingAnswer(false);
        }
    };

    // Start SSE on mount
    useEffect(() => {
        if (!groupId) return;
        void startPlanning();
        return () => {
            abortControllerRef.current?.abort();
        };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const startPlanning = async () => {
        const abort = new AbortController();
        abortControllerRef.current = abort;
        setAgentActive(true);

        const token = session?.access_token;
        const headers: Record<string, string> = {
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        };

        try {
            const res = await fetch(`${API_BASE}/group-trips/${groupId}/plan`, {
                method: "POST",
                headers,
                signal: abort.signal,
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                setPlanError(err.detail ?? "Planning failed");
                setPlanning(false);
                setAgentActive(false);
                return;
            }

            if (!res.body) {
                setPlanError("No streaming response body");
                setPlanning(false);
                setAgentActive(false);
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const { done: streamDone, value } = await reader.read();
                if (streamDone) break;
                buffer += decoder.decode(value, { stream: true });
                const frames = buffer.split("\n\n");
                buffer = frames.pop() ?? "";
                for (const frame of frames) {
                    handleSseFrame(frame);
                }
            }

            if (buffer.trim()) handleSseFrame(buffer.trim());
        } catch (err) {
            if (err instanceof Error && err.name === 'AbortError') return;
            if (!wasCancelledRef.current) {
                setPlanError(err instanceof Error ? err.message : "Planning failed");
            }
        } finally {
            setPlanning(false);
            setAgentActive(false);
        }
    };

    const handleSseFrame = (frame: string) => {
        const parsed = parseSseFrame(frame);
        if (!parsed) return;
        const { event, data } = parsed;

        if (event === "accepted") {
            setAcceptedCount(Number(data.traveler_count ?? 0));
            if (Array.isArray(data.traveler_names)) {
                setAllTravelerNames(data.traveler_names as string[]);
            }
            if (typeof data.request_id === "string") {
                planRequestIdRef.current = data.request_id;
            }
        }
        if (event === "stage_start") {
            const key = String(data.stage ?? "");
            const label = String(data.label ?? key);
            setPlanStages(prev => {
                if (prev.find(s => s.key === key)) return prev;
                return [...prev, { key, label, status: "running" }];
            });
            if (key === "build_scaffold") {
                setAgentActive(true);
            }
        }
        if (event === "stage_done") {
            const key = String(data.stage ?? "");
            setPlanStages(prev => prev.map(s => s.key === key ? { ...s, status: "done" } : s));
        }
        if (event === "agent_action") {
            setAgentActions(prev => [...prev, data as AgentActionEvent]);
            // Update scratchpad if provided in the action
            if (data.scratchpad && typeof data.scratchpad === "string") {
                setAgentScratchpad(data.scratchpad);
            }
        }
        if (event === "agent_question") {
            setPendingQuestion(data as AgentQuestionEvent);
        }
        if (event === "traveler_extracted") {
            const nickname = String(data.nickname ?? "");
            if (nickname) {
                setExtractedTravelers(prev =>
                    prev.includes(nickname) ? prev : [...prev, nickname]
                );
            }
        }
        if (event === "agent_cancelled") {
            wasCancelledRef.current = true;
            setAgentActive(false);
        }
        if (event === "scaffold_ready") {
            setAgentActive(false);
            setAgentScratchpad(String(data.scratchpad ?? ""));
            setScaffoldText(String(data.scaffold_text ?? ""));
            setSessionId(String(data.session_id ?? ""));
            setRevisionCount(Number(data.revision_count ?? 0));
            setMaxRevisions(Number(data.max_revisions ?? 1));
            setScaffoldReady(true);
            // Scroll to bottom to show the scaffold after a short delay for rendering
            setTimeout(() => {
                window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
            }, 100);
        }
        if (event === "error") {
            setPlanError(String(data.message ?? "Planning failed"));
        }
    };

    const handleCancelPlanning = async () => {
        wasCancelledRef.current = true;
        setAgentActive(false);
        abortControllerRef.current?.abort();
        if (planRequestIdRef.current) {
            await cancelPlanningRequest(planRequestIdRef.current);
        }
    };

    const handleRevise = async () => {
        if (!sessionId || !scaffoldText) return;
        setRevisingScaffold(true);
        try {
            const result = await reviseScaffold(sessionId, scaffoldText, scaffoldFeedback);
            setScaffoldText(result.scaffold_text);
            setRevisionCount(result.revision_count);
            setScaffoldFeedback("");
        } catch (err) {
            setPlanError(err instanceof Error ? err.message : "Revision failed");
        } finally {
            setRevisingScaffold(false);
        }
    };

    const handleApprove = async (approvedScaffold: string) => {
        if (!sessionId) return;
        setExtracting(true);
        setScaffoldReady(false);
        setExtractError(null);
        setNodes([]);
        nodeQueueRef.current = [];
        if (drainTimerRef.current !== null) {
            clearInterval(drainTimerRef.current);
            drainTimerRef.current = null;
        }
        try {
            const res = await extractIdeaStream(sessionId, approvedScaffold, {
                onEvent: ({ event, data }) => {
                    if (event === "node_batch") {
                        const incoming = asItineraryNodeArray(data.nodes);
                        if (incoming.length > 0) nodeQueueRef.current.push(...incoming);
                        if (drainTimerRef.current === null) {
                            drainTimerRef.current = setInterval(() => {
                                const next = nodeQueueRef.current.shift();
                                if (next) {
                                    setNodes(prev => [...prev, next]);
                                } else {
                                    clearInterval(drainTimerRef.current!);
                                    drainTimerRef.current = null;
                                }
                            }, 80);
                        }
                    }
                },
            });
            setNodes(res.nodes);
            setDone(true);
            // Navigate to TripPage after successful extraction
            const tripId = groupId;
            if (tripId) {
                sessionStorage.setItem(
                    `nova_session_${tripId}`,
                    JSON.stringify({ agentActions, agentScratchpad }),
                );
                navigate(`/trips/${tripId}`, {
                    state: { groupSize: acceptedCount || undefined },
                });
            }
        } catch (err) {
            setExtractError(err instanceof Error ? err.message : "Extraction failed");
        } finally {
            if (drainTimerRef.current !== null) {
                clearInterval(drainTimerRef.current);
                drainTimerRef.current = null;
            }
            if (nodeQueueRef.current.length > 0) {
                const remaining = [...nodeQueueRef.current];
                nodeQueueRef.current = [];
                setNodes(prev => [...prev, ...remaining]);
            }
            setExtracting(false);
        }
    };

    const dayGroups = groupByDate(nodes);
    const totalActivities = nodes.filter(n => !n.segment_kind || n.segment_kind === "activity").length;

    return (
        <>
            <div className="sky-bg" />
            <div className="sky-arc sky-arc-1" />
            <div className="sky-arc sky-arc-2" />
            <div className="sky-cloud sky-cloud-1" />
            <div className="sky-cloud sky-cloud-2" />
            <div className="sky-cloud sky-cloud-3" />
            <div className="sky-cloud sky-cloud-4" />
            <div className="sky-cloud sky-cloud-5" />
            <button
                className="sky-wordmark"
                onClick={() => navigate("/dashboard")}
                style={{ background: "none", border: "none", cursor: "pointer" }}
            >
                <span className="sky-wordmark-star">✦</span>
                NovaSync
            </button>

            <div className="group-plan-page" style={{ position: "relative", zIndex: 10, maxWidth: 820, margin: "0 auto", padding: "80px 1.5rem 3rem" }}>
                {/* Back button */}
                <button
                    onClick={() => navigate(`/group/${groupId}/waiting`)}
                    style={{ background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 6, color: "#4a6a8a", fontSize: "0.88rem", padding: "4px 0", marginBottom: 24, fontFamily: "'DM Sans', sans-serif" }}
                >
                    <span>←</span> Back to lobby
                </button>

                <h1 style={{ fontFamily: "'Cormorant Garamond', serif", color: "#0b1f38", fontSize: "2rem", margin: "0 0 1.5rem" }}>
                    {done ? "Your Group Itinerary" : "Planning your trip…"}
                </h1>

                {/* Error */}
                {planError && (
                    <div style={{ background: "rgba(192,57,43,0.1)", border: "1px solid rgba(192,57,43,0.3)", borderRadius: 10, padding: "1rem 1.2rem", marginBottom: 20, color: "#c0392b", fontSize: "0.88rem" }}>
                        {planError}
                    </div>
                )}

                {/* Planning progress */}
                {planning && !scaffoldReady && !done && (
                    <div className="glass-card" style={{ padding: "1.6rem 1.8rem", marginBottom: 20 }}>
                        {acceptedCount !== null && (
                            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                <span style={{ color: "#2a9d5c" }}>✓</span>
                                Loaded preferences for <strong>{acceptedCount}</strong> traveller{acceptedCount !== 1 ? "s" : ""}
                            </div>
                        )}
                        {planStages.map(stage => (
                            <div key={stage.key} style={{ marginBottom: 12 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                    {stage.status === "done" ? (
                                        <span style={{ color: "#2a9d5c", fontSize: "1rem", flexShrink: 0 }}>✓</span>
                                    ) : stage.status === "running" ? (
                                        <span style={{ display: "inline-block", width: 16, height: 16, border: "2px solid #4a8dc4", borderTopColor: "transparent", borderRadius: "50%", flexShrink: 0, animation: "spin 0.8s linear infinite" }} />
                                    ) : (
                                        <span style={{ width: 16, height: 16, border: "2px solid #c8ddf0", borderRadius: "50%", flexShrink: 0 }} />
                                    )}
                                    <span style={{ fontSize: "0.88rem", color: stage.status === "done" ? "#4a6a8a" : stage.status === "running" ? "#0b1f38" : "#9fb3c8", fontWeight: stage.status === "running" ? 500 : 400 }}>
                                        {stage.label}
                                        {stage.key === "extract_preferences" && acceptedCount !== null && stage.status === "running" && extractedTravelers.length < acceptedCount && (
                                            <span style={{ marginLeft: 6, color: "#9fb3c8", fontWeight: 400 }}>
                                                {extractedTravelers.length}/{acceptedCount}
                                            </span>
                                        )}
                                    </span>
                                </div>

                                {/* Named checklist for extract_preferences stage */}
                                {stage.key === "extract_preferences" && (
                                    <div className="pref-checklist">
                                        {allTravelerNames.length > 0 ? (
                                            allTravelerNames.map((name, i) => {
                                                const isDone = extractedTravelers.includes(name);
                                                return (
                                                    <div
                                                        key={name}
                                                        className={`pref-row${isDone ? " pref-row--done" : " pref-row--parsing"}`}
                                                        style={{ animationDelay: `${i * 60}ms` }}
                                                    >
                                                        <div className="pref-avatar">
                                                            {name[0]?.toUpperCase() ?? "?"}
                                                            {isDone && (
                                                                <div className="pref-check-icon">✓</div>
                                                            )}
                                                        </div>
                                                        <span className="pref-name">{name}</span>
                                                        {isDone ? (
                                                            <span className="pref-status--done">preferences ready</span>
                                                        ) : (
                                                            <span className="pref-status--parsing">
                                                                <span className="agent-feed-thinking-dots" style={{ color: "#7a9ab8" }}>
                                                                    <span /><span /><span />
                                                                </span>
                                                                parsing
                                                            </span>
                                                        )}
                                                    </div>
                                                );
                                            })
                                        ) : extractedTravelers.length > 0 ? (
                                            /* Fallback: chips if names weren't front-loaded */
                                            <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                                                {extractedTravelers.map((name, i) => (
                                                    <div key={name} className="traveler-chip" style={{ animationDelay: `${i * 55}ms` }}>
                                                        <div className="traveler-chip-avatar">
                                                            {name[0]?.toUpperCase() ?? "?"}
                                                            <div className="traveler-chip-check">✓</div>
                                                        </div>
                                                        <span className="traveler-chip-name" title={name}>{name}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        ) : null}
                                    </div>
                                )}
                            </div>
                        ))}
                        {planStages.length === 0 && (
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                <span style={{ display: "inline-block", width: 16, height: 16, border: "2px solid #4a8dc4", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                                <span style={{ fontSize: "0.88rem", color: "#0b1f38" }}>Starting…</span>
                            </div>
                        )}
                    </div>
                )}

                {/* Agent question card */}
                {pendingQuestion && (
                    <div style={{ marginBottom: 20 }}>
                        <AgentQuestionCard
                            question={pendingQuestion}
                            onAnswer={handleAnswer}
                            isSubmitting={submittingAnswer}
                        />
                    </div>
                )}

                {/* Agent action feed — shows all actions at the top */}
                {(agentActions.length > 0 || agentActive) && !done && (
                    <AgentActionFeed
                        actions={agentActions}
                        isActive={agentActive}
                        onStop={agentActive ? handleCancelPlanning : undefined}
                        scratchpad={agentScratchpad}
                    />
                )}

                {/* Scaffold review — appears at the bottom after all actions */}
                {scaffoldReady && scaffoldText && (
                    <div style={{ marginTop: 20, marginBottom: 20 }}>
                        <ScaffoldReviewCard
                            scaffoldText={scaffoldText}
                            revisionCount={revisionCount}
                            maxRevisions={maxRevisions}
                            feedback={scaffoldFeedback}
                            onFeedbackChange={setScaffoldFeedback}
                            onRevise={handleRevise}
                            onApprove={handleApprove}
                            isRevising={revisingScaffold}
                            scratchpad={agentScratchpad}
                        />
                    </div>
                )}

                {/* Extraction spinner */}
                {extracting && (
                    <div className="glass-card" style={{ padding: "1.6rem 1.8rem", marginTop: 20 }}>
                        <p style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "1.2rem", fontWeight: 600, color: "#0b1f38", margin: "0 0 14px" }}>
                            Generating itinerary…
                        </p>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <span style={{ display: "inline-block", width: 16, height: 16, border: "2px solid #4a8dc4", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                            <span style={{ fontSize: "0.88rem", color: "#4a6a8a" }}>Extracting structured itinerary…</span>
                        </div>
                    </div>
                )}

                {extractError && (
                    <p style={{ color: "#c0392b", fontSize: "0.88rem", marginTop: 12 }}>{extractError}</p>
                )}

                {/* After extraction, we navigate to /trips/:id — no inline rendering */}
            </div>
        </>
    );
}
