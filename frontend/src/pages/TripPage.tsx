import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import AgentActionFeed from "../components/AgentActionFeed";
import { getActivityStyle } from "../lib/activityStyle";
import type { AgentActionEvent, ItineraryNode } from "../types";
import "../styles/sky-theme.css";
import BookingModal from "../components/BookingModal";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

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

export default function TripPage() {
    const { id: tripId } = useParams<{ id: string }>();
    const { session } = useAuth();
    const navigate = useNavigate();

    const [nodes, setNodes] = useState<ItineraryNode[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Agent research data from sessionStorage
    const [agentActions, setAgentActions] = useState<AgentActionEvent[]>([]);
    const [agentScratchpad, setAgentScratchpad] = useState<string>("");
    const [showResearch, setShowResearch] = useState(false);

    // Booking modal state
    const [bookingTarget, setBookingTarget] = useState<{
        restaurantName: string;
        city: string;
        date: string;
        time: string;
        partySize: number;
    } | null>(null);

    // Extract trip city from sessionStorage agent data
    const tripCity = (() => {
        try {
            const raw = sessionStorage.getItem(`nova_session_${tripId}`);
            if (raw) {
                const parsed = JSON.parse(raw);
                return parsed.city ?? parsed.trip_location ?? "Tokyo";
            }
        } catch { /* ignore */ }
        return "Tokyo";
    })();

    // Load nodes from API
    useEffect(() => {
        if (!tripId) return;
        const token = session?.access_token;
        const headers: Record<string, string> = token
            ? { Authorization: `Bearer ${token}` }
            : {};
        fetch(`${API_BASE}/trips/${tripId}/nodes`, { headers })
            .then(async (res) => {
                if (!res.ok) {
                    const err = await res.json().catch(() => ({ detail: res.statusText }));
                    throw new Error(err.detail ?? "Failed to load trip");
                }
                return res.json();
            })
            .then((data: ItineraryNode[]) => {
                setNodes(data);
                setLoading(false);
            })
            .catch((err) => {
                setError(err instanceof Error ? err.message : "Failed to load trip");
                setLoading(false);
            });
    }, [tripId, session]);

    // Load agent data from sessionStorage
    useEffect(() => {
        if (!tripId) return;
        try {
            const raw = sessionStorage.getItem(`nova_session_${tripId}`);
            if (raw) {
                const parsed = JSON.parse(raw);
                setAgentActions(parsed.agentActions || []);
                setAgentScratchpad(parsed.agentScratchpad || "");
            }
        } catch {
            /* ignore */
        }
    }, [tripId]);

    const dayGroups = groupByDate(nodes);
    const dayCount = dayGroups.length;
    const nodeCount = nodes.length;

    if (loading) {
        return (
            <>
                <div className="sky-bg" />
                <div className="sky-arc sky-arc-1" />
                <div className="sky-arc sky-arc-2" />
                <div className="sky-cloud sky-cloud-1" />
                <div className="sky-cloud sky-cloud-2" />
                <button
                    className="sky-wordmark"
                    onClick={() => navigate("/dashboard")}
                    style={{ background: "none", border: "none", cursor: "pointer" }}
                >
                    <span className="sky-wordmark-star">✦</span>
                    NovaSync
                </button>
                <div style={{ position: "relative", zIndex: 10, maxWidth: 820, margin: "0 auto", padding: "120px 1.5rem 3rem", textAlign: "center" }}>
                    <div style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                        <span style={{ display: "inline-block", width: 16, height: 16, border: "2px solid #4a8dc4", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                        <span style={{ fontSize: "1rem", color: "#0b1f38", fontFamily: "'DM Sans', sans-serif" }}>Loading trip...</span>
                    </div>
                </div>
            </>
        );
    }

    if (error) {
        return (
            <>
                <div className="sky-bg" />
                <div className="sky-arc sky-arc-1" />
                <div className="sky-arc sky-arc-2" />
                <button
                    className="sky-wordmark"
                    onClick={() => navigate("/dashboard")}
                    style={{ background: "none", border: "none", cursor: "pointer" }}
                >
                    <span className="sky-wordmark-star">✦</span>
                    NovaSync
                </button>
                <div style={{ position: "relative", zIndex: 10, maxWidth: 820, margin: "0 auto", padding: "120px 1.5rem 3rem" }}>
                    <div style={{ background: "rgba(192,57,43,0.1)", border: "1px solid rgba(192,57,43,0.3)", borderRadius: 10, padding: "1rem 1.2rem", color: "#c0392b", fontSize: "0.88rem" }}>
                        {error}
                    </div>
                    <button
                        onClick={() => navigate("/dashboard")}
                        className="sky-btn-secondary"
                        style={{ marginTop: 20 }}
                    >
                        Back to dashboard
                    </button>
                </div>
            </>
        );
    }

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
                {/* Back to dashboard */}
                <button
                    onClick={() => navigate("/dashboard")}
                    style={{ background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 6, color: "#4a6a8a", fontSize: "0.88rem", padding: "4px 0", marginBottom: 24, fontFamily: "'DM Sans', sans-serif" }}
                >
                    <span>←</span> Back to dashboard
                </button>

                {/* Full-width header card */}
                <div className="trip-result-header">
                    <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "2.5rem", fontWeight: 600, color: "#0b1f38", margin: 0 }}>
                        Your Trip
                    </h1>
                    <div className="trip-result-stats">
                        <span className="trip-result-stat">{dayCount} day{dayCount !== 1 ? "s" : ""}</span>
                        <span className="trip-result-stat">{nodeCount} activit{nodeCount !== 1 ? "ies" : "y"}</span>
                    </div>
                </div>

                {/* Day-grouped activity grid */}
                <div className="trip-result-days">
                    {dayGroups.map(({ date, nodes: dayNodes }, di) => {
                        return (
                            <div key={di} className="trip-result-day">
                                <div className="trip-result-day-header">
                                    Day {di + 1} &mdash; {formatDate(date)}
                                </div>
                                <div className="trip-result-grid">
                                    {dayNodes.map((node, ni) => {
                                        const style = getActivityStyle(node.activity_type);
                                        return (
                                            <div
                                                key={ni}
                                                className="activity-card"
                                                style={{ borderLeftColor: style.color }}
                                            >
                                                <span className="activity-card-icon">{style.emoji}</span>
                                                <div className="activity-card-title">{node.title}</div>
                                                <div className="activity-card-type">{node.activity_type.replace(/_/g, " ")}</div>
                                                {node.description && (
                                                    <div className="activity-card-desc">{node.description}</div>
                                                )}
                                                {(node.activity_type === "restaurant" ||
                                                    node.activity_type.toLowerCase().includes("dining") ||
                                                    node.activity_type.toLowerCase().includes("food") ||
                                                    node.activity_type.toLowerCase().includes("restaurant")) && (
                                                    <button
                                                        type="button"
                                                        className="sky-btn-primary"
                                                        style={{ marginTop: 8, fontSize: "0.78rem", padding: "4px 10px" }}
                                                        onClick={() => setBookingTarget({
                                                            restaurantName: node.title,
                                                            city: tripCity,
                                                            date: node.date_local ?? new Date().toISOString().split("T")[0],
                                                            time: node.start_time_local ?? "19:00",
                                                            partySize: 2,
                                                        })}
                                                    >
                                                        🍽️ Book with Agent
                                                    </button>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        );
                    })}
                </div>

                {/* Collapsible research feed */}
                {agentActions.length > 0 && (
                    <div style={{ marginTop: 32 }}>
                        <button
                            onClick={() => setShowResearch((v) => !v)}
                            style={{
                                background: "rgba(74,141,196,0.08)",
                                border: "1px solid rgba(74,141,196,0.2)",
                                borderRadius: 8,
                                color: "#4a6a8a",
                                padding: "8px 16px",
                                cursor: "pointer",
                                marginBottom: 16,
                                fontFamily: "'DM Sans', sans-serif",
                                fontSize: "0.85rem",
                            }}
                        >
                            {showResearch ? "Research Feed ▾" : "Research Feed ▸"} ({agentActions.length} steps)
                        </button>
                        {showResearch && (
                            <AgentActionFeed actions={agentActions} isActive={false} />
                        )}
                    </div>
                )}
            </div>
            {bookingTarget && (
                <BookingModal
                    isOpen={bookingTarget !== null}
                    onClose={() => setBookingTarget(null)}
                    restaurantName={bookingTarget.restaurantName}
                    city={bookingTarget.city}
                    date={bookingTarget.date}
                    time={bookingTarget.time}
                    partySize={bookingTarget.partySize}
                />
            )}
        </>
    );
}
