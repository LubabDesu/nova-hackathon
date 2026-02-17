// NovaSync — Zone 2: Result Display

import { useMemo, useState } from "react";
import type { ItineraryNode, ProcessIdeaDebugResponse, WorkerReport } from "../types";

interface ResultDisplayProps {
    nodes: ItineraryNode[];
    tripId: string | null;
    error: string | null;
    debugPayload: ProcessIdeaDebugResponse | null;
}

const TYPE_COLORS: Record<string, string> = {
    sightseeing: "#6ee7b7",
    hiking: "#fbbf24",
    food: "#f87171",
    transport: "#93c5fd",
    accommodation: "#c4b5fd",
    adventure: "#fb923c",
    culture: "#f9a8d4",
    shopping: "#a5f3fc",
    relaxation: "#d9f99d",
};

export default function ResultDisplay({
    nodes,
    tripId,
    error,
    debugPayload,
}: ResultDisplayProps) {
    const [activeDebugTab, setActiveDebugTab] = useState<"workers" | "evidence" | "validation">(
        "workers",
    );

    const sortedNodes = [...nodes].sort((a, b) => {
        const dateA = a.date_local ?? "9999-12-31";
        const dateB = b.date_local ?? "9999-12-31";
        if (dateA !== dateB) return dateA.localeCompare(dateB);

        const timeA = a.start_time_local ?? "99:99";
        const timeB = b.start_time_local ?? "99:99";
        if (timeA !== timeB) return timeA.localeCompare(timeB);

        return 0;
    });

    const workerReports = debugPayload?.worker_reports ?? [];
    const evidence = debugPayload?.evidence ?? [];
    const validationReport = debugPayload?.validation_report;
    const validationErrors = validationReport?.errors ?? [];
    const validationWarnings = validationReport?.warnings ?? [];
    const validationActivities = validationReport?.plan?.activities ?? [];
    const planWarnings = validationReport?.plan?.warnings ?? [];
    const planAssumptions = validationReport?.plan?.assumptions ?? [];

    const workerStats = useMemo(() => {
        const stats = {
            success: 0,
            skipped: 0,
            failed: 0,
            evidenceAdded: 0,
        };

        for (const report of workerReports) {
            const status = report.status.toUpperCase();
            if (status === "SUCCESS") {
                stats.success += 1;
            } else if (status === "SKIPPED") {
                stats.skipped += 1;
            } else {
                stats.failed += 1;
            }
            stats.evidenceAdded += report.evidence_added;
        }

        return stats;
    }, [workerReports]);

    const evidenceBySource = useMemo(() => {
        const counts = new Map<string, number>();
        for (const item of evidence) {
            counts.set(item.source_type, (counts.get(item.source_type) ?? 0) + 1);
        }
        return [...counts.entries()].sort((a, b) => b[1] - a[1]);
    }, [evidence]);

    const groundedEvidenceCount = evidence.filter(
        (item) => item.id.startsWith("ev_worker_grounded_web_"),
    ).length;
    const lowConfidenceEvidenceCount = evidence.filter(
        (item) => item.confidence < 0.5,
    ).length;

    const debugSection = debugPayload ? (
        <details className="debug-panel">
            <summary>Debug Inspector</summary>
            <div className="debug-inspector">
                <div className="debug-tabs">
                    <button
                        type="button"
                        className={`debug-tab ${activeDebugTab === "workers" ? "is-active" : ""}`}
                        onClick={() => setActiveDebugTab("workers")}
                    >
                        Workers ({workerReports.length})
                    </button>
                    <button
                        type="button"
                        className={`debug-tab ${activeDebugTab === "evidence" ? "is-active" : ""}`}
                        onClick={() => setActiveDebugTab("evidence")}
                    >
                        Evidence ({evidence.length})
                    </button>
                    <button
                        type="button"
                        className={`debug-tab ${activeDebugTab === "validation" ? "is-active" : ""}`}
                        onClick={() => setActiveDebugTab("validation")}
                    >
                        Validation ({validationErrors.length + validationWarnings.length})
                    </button>
                </div>

                {activeDebugTab === "workers" && (
                    <div className="debug-content">
                        <div className="debug-stats-row">
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Success</p>
                                <p className="debug-stat-value">{workerStats.success}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Skipped</p>
                                <p className="debug-stat-value">{workerStats.skipped}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Other Status</p>
                                <p className="debug-stat-value">{workerStats.failed}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Evidence Added</p>
                                <p className="debug-stat-value">{workerStats.evidenceAdded}</p>
                            </div>
                        </div>

                        {workerReports.length === 0 ? (
                            <p className="debug-empty-message">No worker reports.</p>
                        ) : (
                            <div className="worker-report-list">
                                {workerReports.map((report, index) => (
                                    <article
                                        className="worker-report-card"
                                        key={`${report.worker_name}-${index}`}
                                    >
                                        <div className="worker-report-head">
                                            <h4>{report.worker_name}</h4>
                                            <StatusPill status={report.status} />
                                        </div>
                                        <p className="worker-report-metric">
                                            evidence_added: {report.evidence_added}
                                        </p>
                                        {report.notes && (
                                            <p className="worker-report-notes">{report.notes}</p>
                                        )}
                                    </article>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {activeDebugTab === "evidence" && (
                    <div className="debug-content">
                        <div className="debug-stats-row">
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Total Evidence</p>
                                <p className="debug-stat-value">{evidence.length}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Grounded Web</p>
                                <p className="debug-stat-value">{groundedEvidenceCount}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Low Confidence</p>
                                <p className="debug-stat-value">{lowConfidenceEvidenceCount}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Source Types</p>
                                <p className="debug-stat-value">{evidenceBySource.length}</p>
                            </div>
                        </div>

                        {evidenceBySource.length > 0 && (
                            <div className="evidence-source-breakdown">
                                {evidenceBySource.map(([source, count]) => (
                                    <span className="evidence-source-pill" key={source}>
                                        {source}: {count}
                                    </span>
                                ))}
                            </div>
                        )}

                        {evidence.length === 0 ? (
                            <p className="debug-empty-message">No evidence captured.</p>
                        ) : (
                            <div className="evidence-list">
                                {evidence.map((item) => (
                                    <article className="evidence-card" key={item.id}>
                                        <div className="evidence-card-head">
                                            <h4>{item.id}</h4>
                                            <span className="evidence-meta">
                                                {item.source_type} | conf {Math.round(item.confidence * 100)}%
                                            </span>
                                        </div>
                                        <p className="evidence-summary">{item.summary}</p>
                                        {item.facts.time_hints.length > 0 && (
                                            <p className="evidence-facts-line">
                                                <strong>time_hints:</strong>{" "}
                                                {item.facts.time_hints.join(" | ")}
                                            </p>
                                        )}
                                        {item.facts.constraints.length > 0 && (
                                            <p className="evidence-facts-line">
                                                <strong>constraints:</strong>{" "}
                                                {item.facts.constraints.join(" | ")}
                                            </p>
                                        )}
                                        {item.citations.length > 0 && (
                                            <div className="evidence-citations">
                                                {item.citations.map((citation, index) => (
                                                    <a
                                                        href={citation}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        key={`${item.id}-citation-${index}`}
                                                    >
                                                        {formatCitation(citation)}
                                                    </a>
                                                ))}
                                            </div>
                                        )}
                                        {item.debug?.fetch_status && (
                                            <p className="evidence-debug-line">
                                                fetch_status: {item.debug.fetch_status}
                                            </p>
                                        )}
                                    </article>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {activeDebugTab === "validation" && (
                    <div className="debug-content">
                        <div className="debug-stats-row">
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Errors</p>
                                <p className="debug-stat-value">{validationErrors.length}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Warnings</p>
                                <p className="debug-stat-value">{validationWarnings.length}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Plan Activities</p>
                                <p className="debug-stat-value">{validationActivities.length}</p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Assumptions</p>
                                <p className="debug-stat-value">{planAssumptions.length}</p>
                            </div>
                        </div>

                        <ValidationMessages title="Errors" messages={validationErrors} />
                        <ValidationMessages title="Warnings" messages={validationWarnings} />
                        <ValidationMessages title="Plan Warnings" messages={planWarnings} />
                        <ValidationMessages title="Assumptions" messages={planAssumptions} />

                        {validationActivities.length > 0 && (
                            <div className="validation-activity-list">
                                {validationActivities.map((activity, index) => (
                                    <article
                                        className="validation-activity-card"
                                        key={`${activity.title}-${index}`}
                                    >
                                        <div className="validation-activity-head">
                                            <h4>{activity.title}</h4>
                                            <span>
                                                {activity.date_local ?? "No date"} |{" "}
                                                {activity.start_time_local ?? "--:--"} -{" "}
                                                {activity.end_time_local ?? "--:--"}
                                            </span>
                                        </div>
                                        <p className="validation-activity-type">
                                            type: {activity.activity_type}
                                        </p>
                                        {activity.validation_notes.length > 0 && (
                                            <ul className="validation-list">
                                                {activity.validation_notes.map((note, noteIndex) => (
                                                    <li key={`${activity.title}-note-${noteIndex}`}>
                                                        {note}
                                                    </li>
                                                ))}
                                            </ul>
                                        )}
                                        {activity.source_evidence_ids.length > 0 && (
                                            <p className="validation-evidence-ids">
                                                source_evidence_ids:{" "}
                                                {activity.source_evidence_ids.join(", ")}
                                            </p>
                                        )}
                                    </article>
                                ))}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </details>
    ) : null;

    if (error) {
        return (
            <div className="result-zone">
                <div className="error-card">
                    <span className="error-icon">⚠️</span>
                    <p>{error}</p>
                </div>
                {debugSection}
            </div>
        );
    }

    if (nodes.length === 0) {
        return (
            <div className="result-zone empty-state">
                <div className="empty-graphic">🗺️</div>
                <h3>Your itinerary will appear here</h3>
                <p>Submit your travel ideas on the left to get started.</p>
                {debugSection}
            </div>
        );
    }

    return (
        <div className="result-zone">
            <div className="result-header">
                <h2 className="zone-title">
                    <span className="zone-icon">📋</span> Extracted Itinerary
                </h2>
                {tripId && (
                    <span className="trip-badge">
                        Trip: {tripId.slice(0, 8)}…
                    </span>
                )}
            </div>
            <div className="nodes-grid">
                {sortedNodes.map((node, i) => (
                    <div className="node-card" key={i}>
                        {(node.date_local || node.start_time_local || node.end_time_local) && (
                            <div className="schedule-row">
                                <span className="schedule-date">
                                    {node.date_local ?? "Unscheduled date"}
                                </span>
                                {(node.start_time_local || node.end_time_local) && (
                                    <span className="schedule-time">
                                        {node.start_time_local ?? "??:??"} -{" "}
                                        {node.end_time_local ?? "??:??"}
                                    </span>
                                )}
                            </div>
                        )}
                        <div className="node-card-header">
                            <span
                                className="activity-badge"
                                style={{
                                    backgroundColor:
                                        TYPE_COLORS[node.activity_type] ??
                                        "#94a3b8",
                                }}
                            >
                                {node.activity_type}
                            </span>
                            {node.duration_mins && (
                                <span className="duration-pill">
                                    ⏱ {node.duration_mins} min
                                </span>
                            )}
                        </div>
                        <h3 className="node-title">{node.title}</h3>
                        {node.description && (
                            <p className="node-desc">{node.description}</p>
                        )}
                        {node.lat != null && node.long != null && (
                            <span className="coords">
                                📍 {node.lat.toFixed(4)}, {node.long.toFixed(4)}
                            </span>
                        )}
                    </div>
                ))}
            </div>
            {debugSection}
        </div>
    );
}

function StatusPill({ status }: { status: WorkerReport["status"] }) {
    const normalized = status.toUpperCase();
    let className = "status-pill";

    if (normalized === "SUCCESS") {
        className += " is-success";
    } else if (normalized === "SKIPPED") {
        className += " is-skipped";
    } else {
        className += " is-other";
    }

    return <span className={className}>{status}</span>;
}

function ValidationMessages({
    title,
    messages,
}: {
    title: string;
    messages: string[];
}) {
    if (messages.length === 0) {
        return null;
    }

    return (
        <section className="validation-message-block">
            <h4>{title}</h4>
            <ul className="validation-list">
                {messages.map((message, index) => (
                    <li key={`${title}-${index}`}>{message}</li>
                ))}
            </ul>
        </section>
    );
}

function formatCitation(url: string): string {
    try {
        const parsed = new URL(url);
        const path = parsed.pathname === "/" ? "" : parsed.pathname;
        return `${parsed.hostname}${path}`;
    } catch {
        return url;
    }
}
