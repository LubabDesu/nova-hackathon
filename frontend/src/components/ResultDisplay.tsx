// NovaSync — Zone 2: Result Display

import { useEffect, useMemo, useRef, useState } from "react";
import type { ItineraryNode, ProcessIdeaDebugResponse, WorkerReport } from "../types";

interface ResultDisplayProps {
    nodes: ItineraryNode[];
    tripId: string | null;
    plannerReasoning: string | null;
    error: string | null;
    debugPayload: ProcessIdeaDebugResponse | null;
    loading: boolean;
    loadingPhaseLabel?: string;
    loadingPhaseDetail?: string;
    loadingStepIndex?: number;
    loadingTotalSteps?: number;
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
    plannerReasoning,
    error,
    debugPayload,
    loading,
    loadingPhaseLabel,
    loadingPhaseDetail,
    loadingStepIndex,
    loadingTotalSteps,
}: ResultDisplayProps) {
    const [activeDebugTab, setActiveDebugTab] = useState<"workers" | "evidence" | "validation" | "trace">(
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
    const dayGroups = useMemo(() => {
        const buckets = new Map<string, ItineraryNode[]>();
        for (const node of sortedNodes) {
            const key = node.date_local ?? "unscheduled";
            const existing = buckets.get(key);
            if (existing) {
                existing.push(node);
            } else {
                buckets.set(key, [node]);
            }
        }

        const sortedKeys = [...buckets.keys()].sort((a, b) => {
            if (a === "unscheduled") return 1;
            if (b === "unscheduled") return -1;
            return a.localeCompare(b);
        });

        return sortedKeys.map((key) => {
            const items = buckets.get(key) ?? [];
            let earliest: string | null = null;
            let latest: string | null = null;
            let totalDuration = 0;

            for (const item of items) {
                if (item.duration_mins) {
                    totalDuration += item.duration_mins;
                }
                if (item.start_time_local) {
                    if (!earliest || item.start_time_local < earliest) {
                        earliest = item.start_time_local;
                    }
                }
                if (item.end_time_local) {
                    if (!latest || item.end_time_local > latest) {
                        latest = item.end_time_local;
                    }
                }
            }

            return {
                key,
                label: formatDateLabel(key),
                items,
                earliest,
                latest,
                totalDuration,
            };
        });
    }, [sortedNodes]);

    const workerReports = debugPayload?.worker_reports ?? [];
    const evidence = debugPayload?.evidence ?? [];
    const validationReport = debugPayload?.validation_report;
    const validationErrors = validationReport?.errors ?? [];
    const validationWarnings = validationReport?.warnings ?? [];
    const validationActivities = validationReport?.plan?.activities ?? [];
    const planWarnings = validationReport?.plan?.warnings ?? [];
    const planAssumptions = validationReport?.plan?.assumptions ?? [];
    const debugTrace = debugPayload?.debug_trace;
    const plannerScaffoldText =
        (typeof plannerReasoning === "string" && plannerReasoning.trim().length > 0
            ? plannerReasoning.trim()
            : null)
        ?? (typeof debugTrace?.planner_scaffold_text === "string"
            && debugTrace.planner_scaffold_text.trim().length > 0
            ? debugTrace.planner_scaffold_text.trim()
            : null);
    const plannerScaffoldDebug = debugTrace?.planner_scaffold_debug;
    const qwenMediaSignals = debugTrace?.qwen_media_signals ?? [];
    const plannerEvidenceSelected = debugTrace?.planner_evidence_selected ?? [];
    const plannerEvidenceLines = debugTrace?.planner_evidence_lines ?? [];
    const plannerEvidenceBudget = debugTrace?.planner_evidence_budget;
    const webQueryDebug = debugTrace?.web_query_builder;
    const executedQueries = webQueryDebug?.queries_executed ?? [];
    const queryOutcomes = webQueryDebug?.query_outcomes ?? [];

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
                    <button
                        type="button"
                        className={`debug-tab ${activeDebugTab === "trace" ? "is-active" : ""}`}
                        onClick={() => setActiveDebugTab("trace")}
                    >
                        Trace
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

                {activeDebugTab === "trace" && (
                    <div className="debug-content">
                        <div className="debug-stats-row">
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Qwen Signals</p>
                                <p className="debug-stat-value">
                                    {debugTrace?.qwen_media_signal_count ?? qwenMediaSignals.length}
                                </p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Planner Evidence</p>
                                <p className="debug-stat-value">
                                    {plannerEvidenceSelected.length}
                                </p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Prompt Chars</p>
                                <p className="debug-stat-value">
                                    {debugTrace?.planner_prompt_chars ?? 0}
                                </p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Scaffold Chars</p>
                                <p className="debug-stat-value">
                                    {plannerScaffoldDebug?.scaffold_chars ?? 0}
                                </p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Budget</p>
                                <p className="debug-stat-value">
                                    {plannerEvidenceBudget
                                        ? `${plannerEvidenceBudget.selected_items}/${plannerEvidenceBudget.max_items}`
                                        : "N/A"}
                                </p>
                            </div>
                            <div className="debug-stat-card">
                                <p className="debug-stat-label">Query Source</p>
                                <p className="debug-stat-value">
                                    {webQueryDebug?.query_source ?? "N/A"}
                                </p>
                            </div>
                        </div>

                        {debugTrace?.qwen_media_error && (
                            <p className="debug-empty-message">
                                qwen_media_error: {debugTrace.qwen_media_error}
                            </p>
                        )}

                        <section className="validation-message-block">
                            <h4>Qwen Media Signals</h4>
                            {qwenMediaSignals.length === 0 ? (
                                <p className="debug-empty-message">No visual signals extracted.</p>
                            ) : (
                                <div className="evidence-list">
                                    {qwenMediaSignals.map((signal, index) => (
                                        <article className="evidence-card" key={`qwen-signal-${index}`}>
                                            <div className="evidence-card-head">
                                                <h4>{signal.source_filename ?? `signal_${index + 1}`}</h4>
                                                <span className="evidence-meta">
                                                    conf {Math.round(signal.confidence * 100)}%
                                                </span>
                                            </div>
                                            <p className="evidence-summary">{signal.summary}</p>
                                            {signal.location_cues.length > 0 && (
                                                <p className="evidence-facts-line">
                                                    <strong>location_cues:</strong>{" "}
                                                    {signal.location_cues.join(" | ")}
                                                </p>
                                            )}
                                            {signal.activity_hints.length > 0 && (
                                                <p className="evidence-facts-line">
                                                    <strong>activity_hints:</strong>{" "}
                                                    {signal.activity_hints.join(" | ")}
                                                </p>
                                            )}
                                            {signal.vibe_tags.length > 0 && (
                                                <p className="evidence-facts-line">
                                                    <strong>vibe_tags:</strong>{" "}
                                                    {signal.vibe_tags.join(" | ")}
                                                </p>
                                            )}
                                            {signal.constraints.length > 0 && (
                                                <p className="evidence-facts-line">
                                                    <strong>constraints:</strong>{" "}
                                                    {signal.constraints.join(" | ")}
                                                </p>
                                            )}
                                        </article>
                                    ))}
                                </div>
                            )}
                        </section>

                        <section className="validation-message-block">
                            <h4>Planner Reasoning Scaffold</h4>
                            <p className="evidence-summary">
                                enabled={String(plannerScaffoldDebug?.scaffold_enabled ?? false)}
                                {" | "}
                                model={plannerScaffoldDebug?.scaffold_model_selected ?? plannerScaffoldDebug?.scaffold_model_requested ?? "N/A"}
                                {" | "}
                                response_model={plannerScaffoldDebug?.response_model ?? "N/A"}
                            </p>
                            {plannerScaffoldDebug?.error && (
                                <p className="debug-empty-message">
                                    scaffold_error: {plannerScaffoldDebug.error}
                                </p>
                            )}
                            {plannerScaffoldText ? (
                                <pre className="reasoning-pre">{plannerScaffoldText}</pre>
                            ) : (
                                <p className="debug-empty-message">
                                    No planner scaffold text captured.
                                </p>
                            )}
                        </section>

                        <section className="validation-message-block">
                            <h4>Planner Evidence Pack</h4>
                            {plannerEvidenceBudget && (
                                <p className="evidence-summary">
                                    selected_items={plannerEvidenceBudget.selected_items}, max_items={plannerEvidenceBudget.max_items}, selected_chars={plannerEvidenceBudget.selected_chars}, max_chars={plannerEvidenceBudget.max_chars}
                                </p>
                            )}
                            {plannerEvidenceSelected.length === 0 ? (
                                <p className="debug-empty-message">No selected planner evidence.</p>
                            ) : (
                                <div className="evidence-list">
                                    {plannerEvidenceSelected.map((item) => (
                                        <article className="evidence-card" key={`planner-pack-${item.id}`}>
                                            <div className="evidence-card-head">
                                                <h4>{item.id}</h4>
                                                <span className="evidence-meta">
                                                    {item.source_type} | conf {Math.round(item.confidence * 100)}%
                                                </span>
                                            </div>
                                            <p className="evidence-summary">{item.summary}</p>
                                        </article>
                                    ))}
                                </div>
                            )}
                        </section>

                        <section className="validation-message-block">
                            <h4>Planner Evidence Lines</h4>
                            {plannerEvidenceLines.length === 0 ? (
                                <p className="debug-empty-message">No rendered evidence lines.</p>
                            ) : (
                                <ul className="validation-list">
                                    {plannerEvidenceLines.map((line, index) => (
                                        <li key={`planner-line-${index}`}>{line}</li>
                                    ))}
                                </ul>
                            )}
                        </section>

                        <section className="validation-message-block">
                            <h4>Web Query Builder</h4>
                            <p className="evidence-summary">
                                model={webQueryDebug?.model_debug?.query_builder_model ?? "N/A"} | source={webQueryDebug?.query_source ?? "N/A"} | provider={webQueryDebug?.provider_requested ?? webQueryDebug?.provider ?? "N/A"}
                            </p>
                            <p className="evidence-summary">
                                tavily_key_present={String(webQueryDebug?.tavily_key_present ?? false)} | brave_key_present={String(webQueryDebug?.brave_key_present ?? false)}
                            </p>
                            {webQueryDebug?.fallback_reason && (
                                <p className="debug-empty-message">
                                    fallback_reason: {webQueryDebug.fallback_reason}
                                </p>
                            )}
                            {webQueryDebug?.model_debug?.error && (
                                <p className="debug-empty-message">
                                    query_builder_error: {webQueryDebug.model_debug.error}
                                </p>
                            )}
                            {webQueryDebug?.model_debug?.error_body && (
                                <p className="debug-empty-message">
                                    openrouter_error_body: {webQueryDebug.model_debug.error_body}
                                </p>
                            )}
                            {webQueryDebug?.model_debug?.attempts && webQueryDebug.model_debug.attempts.length > 0 && (
                                <ul className="validation-list">
                                    {webQueryDebug.model_debug.attempts.map((attempt, index) => (
                                        <li key={`query-builder-attempt-${index}`}>
                                            model={attempt.model}, include_temperature={String(attempt.include_temperature)}, status={attempt.status}
                                            {attempt.http_status ? `, http_status=${attempt.http_status}` : ""}
                                            {attempt.error ? `, error=${attempt.error}` : ""}
                                        </li>
                                    ))}
                                </ul>
                            )}
                            {executedQueries.length === 0 ? (
                                <p className="debug-empty-message">No executed queries recorded.</p>
                            ) : (
                                <ul className="validation-list">
                                    {executedQueries.map((query, index) => (
                                        <li key={`executed-query-${index}`}>{query}</li>
                                    ))}
                                </ul>
                            )}
                            {queryOutcomes.length > 0 && (
                                <div className="evidence-list">
                                    {queryOutcomes.map((outcome, index) => (
                                        <article className="evidence-card" key={`query-outcome-${index}`}>
                                            <div className="evidence-card-head">
                                                <h4>{outcome.query}</h4>
                                                <span className="evidence-meta">
                                                    {outcome.provider} | {outcome.result_count} results
                                                </span>
                                            </div>
                                            {outcome.error && (
                                                <p className="evidence-summary">error: {outcome.error}</p>
                                            )}
                                            {outcome.citations.length > 0 && (
                                                <div className="evidence-citations">
                                                    {outcome.citations.map((citation, citationIndex) => (
                                                        <a
                                                            href={citation}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            key={`query-${index}-citation-${citationIndex}`}
                                                        >
                                                            {formatCitation(citation)}
                                                        </a>
                                                    ))}
                                                </div>
                                            )}
                                        </article>
                                    ))}
                                </div>
                            )}
                        </section>
                    </div>
                )}
            </div>
        </details>
    ) : null;

    const reasoningSection = plannerScaffoldText ? (
        <section className="reasoning-panel">
            <header className="reasoning-head">
                <p className="reasoning-kicker">Planner Reasoning</p>
                <span className="reasoning-badge">Draft before JSON extraction</span>
            </header>
            <pre className="reasoning-pre">{plannerScaffoldText}</pre>
        </section>
    ) : null;

    if (error) {
        return (
            <div className="result-zone">
                <div className="error-card">
                    <span className="error-icon">⚠️</span>
                    <p>{error}</p>
                </div>
                {reasoningSection}
                {debugSection}
            </div>
        );
    }

    if (nodes.length === 0) {
        return (
            <div className="result-zone empty-state">
                {loading ? (
                    <PlanningStatusCard
                        loadingPhaseLabel={loadingPhaseLabel}
                        loadingPhaseDetail={loadingPhaseDetail}
                        loadingStepIndex={loadingStepIndex}
                        loadingTotalSteps={loadingTotalSteps}
                    />
                ) : (
                    <>
                        <div className="empty-graphic">🗺️</div>
                        <h3>Your itinerary will appear here</h3>
                        <p>Submit your travel ideas on the left to get started.</p>
                    </>
                )}
                {reasoningSection}
                {debugSection}
            </div>
        );
    }

    return (
        <div className="result-zone">
            {loading && (
                <PlanningStatusCard
                    loadingPhaseLabel={loadingPhaseLabel}
                    loadingPhaseDetail={loadingPhaseDetail}
                    loadingStepIndex={loadingStepIndex}
                    loadingTotalSteps={loadingTotalSteps}
                />
            )}
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
            {reasoningSection}
            <div className="day-groups">
                {dayGroups.map((group, dayIndex) => (
                    <section className="day-group" key={group.key}>
                        <header className="day-group-header">
                            <div>
                                <p className="day-group-kicker">Day {dayIndex + 1}</p>
                                <h3 className="day-group-title">{group.label}</h3>
                                <p className="day-group-meta">
                                    {group.items.length} activities
                                    {group.totalDuration > 0
                                        ? ` • ${group.totalDuration} min planned`
                                        : " • duration TBD"}
                                </p>
                            </div>
                            <span
                                className={`day-group-window ${!group.earliest && !group.latest ? "is-unscheduled" : ""}`}
                            >
                                {group.earliest || group.latest
                                    ? `${group.earliest ?? "??:??"} - ${group.latest ?? "??:??"}`
                                    : "No fixed times"}
                            </span>
                        </header>

                        <div className="nodes-grid day-nodes-grid">
                            {group.items.map((node, index) => (
                                <div
                                    className={`node-card day-node-card ${isFillerNode(node) ? "is-filler-node" : ""}`}
                                    key={`${group.key}-${node.title}-${index}`}
                                >
                                    <div className="schedule-row">
                                        <span className="schedule-time">
                                            {node.start_time_local || node.end_time_local
                                                ? `${node.start_time_local ?? "??:??"} - ${node.end_time_local ?? "??:??"}`
                                                : "Flexible timing"}
                                        </span>
                                    </div>
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
                                        {node.segment_origin === "synthetic" && (
                                            <span className="synthetic-pill">
                                                Auto-filled {node.segment_kind ?? "segment"}
                                            </span>
                                        )}
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
                    </section>
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

function formatDateLabel(value: string): string {
    if (value === "unscheduled") {
        return "Unscheduled";
    }

    const parsed = new Date(`${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) {
        return value;
    }

    return new Intl.DateTimeFormat(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        year: "numeric",
    }).format(parsed);
}

function isFillerNode(node: ItineraryNode): boolean {
    if (node.segment_origin === "synthetic") {
        return true;
    }
    const blob = `${node.title} ${node.description ?? ""}`.toLowerCase();
    return (
        blob.includes("auto-generated")
        || blob.includes("free time")
        || blob.includes("wind-down")
        || blob.includes("flexible")
        || blob.includes("travel from")
        || blob.includes("return from")
    );
}

const THINKING_SUBSTAGES = [
    {
        icon: "🔍",
        label: "Searching the web",
        detail: "Building targeted queries and scanning sources",
        duration: 9000,
    },
    {
        icon: "📖",
        label: "Reading pages",
        detail: "Extracting planning facts from web sources",
        duration: 11000,
    },
    {
        icon: "🧠",
        label: "Grounding evidence",
        detail: "Cross-referencing facts and scheduling constraints",
        duration: 9000,
    },
    {
        icon: "✈️",
        label: "Planning your itinerary",
        detail: "Synthesizing evidence into a coherent day-by-day schedule",
        duration: 13000,
    },
    {
        icon: "📅",
        label: "Building the schedule",
        detail: "Arranging activities, timing, and transitions",
        duration: 10000,
    },
    {
        icon: "✨",
        label: "Almost there",
        detail: "Validating, finalising, and preparing your plan",
        duration: 99999,
    },
] as const;

const FINAL_STAGE_PHRASES = [
    { icon: "✨", label: "Almost there", detail: "Validating, finalising, and preparing your plan" },
    { icon: "🗺️", label: "Polishing the details", detail: "Making sure every transition makes sense" },
    { icon: "⏱️", label: "Double-checking timings", detail: "Ensuring the schedule flows smoothly" },
    { icon: "🏨", label: "Verifying locations", detail: "Cross-checking places and distances" },
    { icon: "✅", label: "Nearly ready", detail: "Putting the finishing touches on your itinerary" },
] as const;

function PlanningStatusCard({
    loadingPhaseLabel,
    loadingPhaseDetail,
    loadingStepIndex,
    loadingTotalSteps,
}: {
    loadingPhaseLabel?: string;
    loadingPhaseDetail?: string;
    loadingStepIndex?: number;
    loadingTotalSteps?: number;
}) {
    const isThinking = loadingPhaseLabel?.toLowerCase().includes("thinking")
        || loadingPhaseLabel?.toLowerCase().includes("parsing")
        || loadingPhaseLabel?.toLowerCase().includes("grounding")
        || loadingPhaseLabel?.toLowerCase().includes("building")
        || loadingPhaseLabel?.toLowerCase().includes("model");

    const [substageIndex, setSubstageIndex] = useState(0);
    const [visible, setVisible] = useState(true);
    const [finalPhraseIndex, setFinalPhraseIndex] = useState(0);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const finalTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const isLastStage = substageIndex === THINKING_SUBSTAGES.length - 1;

    useEffect(() => {
        if (!isThinking) {
            setSubstageIndex(0);
            setVisible(true);
            return;
        }

        let current = 0;
        setSubstageIndex(0);
        setVisible(true);

        const advance = () => {
            setVisible(false);
            setTimeout(() => {
                current = Math.min(current + 1, THINKING_SUBSTAGES.length - 1);
                setSubstageIndex(current);
                setVisible(true);
                if (current < THINKING_SUBSTAGES.length - 1) {
                    timerRef.current = setTimeout(advance, THINKING_SUBSTAGES[current].duration);
                }
            }, 350);
        };

        timerRef.current = setTimeout(advance, THINKING_SUBSTAGES[0].duration);

        return () => {
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isThinking]);

    useEffect(() => {
        if (!isLastStage) {
            if (finalTimerRef.current) {
                clearInterval(finalTimerRef.current);
                finalTimerRef.current = null;
            }
            setFinalPhraseIndex(0);
            return;
        }
        finalTimerRef.current = setInterval(() => {
            setVisible(false);
            setTimeout(() => {
                setFinalPhraseIndex(i => (i + 1) % FINAL_STAGE_PHRASES.length);
                setVisible(true);
            }, 350);
        }, 4500);
        return () => {
            if (finalTimerRef.current) {
                clearInterval(finalTimerRef.current);
                finalTimerRef.current = null;
            }
        };
    }, [isLastStage]);

    if (isThinking) {
        const substage = isLastStage
            ? FINAL_STAGE_PHRASES[finalPhraseIndex]
            : THINKING_SUBSTAGES[substageIndex];
        return (
            <div className="planning-thinking-card">
                <div className="thinking-glow" />
                <div className="thinking-top-row">
                    <span className={`thinking-icon ${visible ? "thinking-fade-in" : "thinking-fade-out"}`}>
                        {substage.icon}
                    </span>
                    <div className="thinking-dots">
                        <span /><span /><span />
                    </div>
                </div>
                <p className={`thinking-label ${visible ? "thinking-fade-in" : "thinking-fade-out"}`}>
                    {substage.label}
                </p>
                <p className={`thinking-detail ${visible ? "thinking-fade-in" : "thinking-fade-out"}`}>
                    {substage.detail}
                </p>
            </div>
        );
    }

    return (
        <div className="planning-status-banner">
            <p className="planning-status-kicker">
                Planner in progress
                {typeof loadingStepIndex === "number"
                    && typeof loadingTotalSteps === "number"
                    ? ` • Step ${loadingStepIndex + 1} of ${loadingTotalSteps}`
                    : ""}
            </p>
            <p className="planning-status-title">
                {loadingPhaseLabel ?? "Generating itinerary"}
            </p>
            <p className="planning-status-detail">
                {loadingPhaseDetail ?? "Running planning pipeline..."}
            </p>
        </div>
    );
}
