// NovaSync — Agent action feed component
// Shows real-time tool calls during the Nova agent planning phase.
// Includes collapsible view, research progress tracking, and a Stop button.

import { useEffect, useRef, useState } from "react";
import type { AgentActionEvent } from "../types";
import ResearchProgressTracker from "./ResearchProgressTracker";

interface AgentActionFeedProps {
    actions: AgentActionEvent[];
    isActive: boolean;
    onStop?: () => void;
    scratchpad?: string;
}

const TOOL_ICONS: Record<string, string> = {
    search_activities: "🔍",
    get_local_events: "🎭",
    get_weather: "🌤️",
    validate_place: "📍",
    write_to_scratchpad: "📝",
    self_critique_plan: "🔍",
    finalize_plan: "✅",
    ask_user: "❓",
};

function getToolIcon(toolName: string): string {
    return TOOL_ICONS[toolName] ?? "⚙️";
}

function formatElapsed(ms: number | undefined): string {
    if (ms === undefined) return "";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

function formatToolInput(input: Record<string, unknown> | undefined): string {
    if (!input || Object.keys(input).length === 0) return "";
    return Object.entries(input)
        .map(([k, v]) => `${k}: ${String(v)}`)
        .join(" · ");
}

function ActionItem({ action }: { action: AgentActionEvent }) {
    const [expanded, setExpanded] = useState(false);
    const [showReasoning, setShowReasoning] = useState(false);
    const hasDetail = !!(action.result_preview || action.tool_input);
    const hasReasoning = !!(action.reasoning && action.reasoning.trim().length > 0);

    return (
        <div className="agent-feed-item">
            <span className="agent-feed-icon">{getToolIcon(action.tool_name)}</span>
            <div className="agent-feed-content">
                <div className="agent-feed-row">
                    <span className="agent-feed-summary">{action.summary}</span>
                    <div className="agent-feed-meta">
                        {action.iteration !== undefined && (
                            <span className="agent-feed-badge agent-feed-badge--iter">
                                #{action.iteration}
                            </span>
                        )}
                        {action.elapsed_ms !== undefined && action.tool_name !== "finalize_plan" && (
                            <span className="agent-feed-badge agent-feed-badge--time">
                                {formatElapsed(action.elapsed_ms)}
                            </span>
                        )}
                        {hasReasoning && (
                            <button
                                type="button"
                                className={`agent-feed-reasoning-toggle ${showReasoning ? "active" : ""}`}
                                onClick={() => setShowReasoning(!showReasoning)}
                                title={showReasoning ? "Hide reasoning" : "Show reasoning"}
                            >
                                💭
                            </button>
                        )}
                        {hasDetail && (
                            <button
                                type="button"
                                className="agent-feed-expand"
                                onClick={() => setExpanded(!expanded)}
                                title={expanded ? "Collapse" : "Expand"}
                            >
                                {expanded ? "▾" : "▸"}
                            </button>
                        )}
                    </div>
                </div>

                {showReasoning && action.reasoning && (
                    <div className="agent-feed-reasoning-block">
                        <span className="agent-feed-reasoning-label">Reasoning</span>
                        <span className="agent-feed-reasoning-text">{action.reasoning}</span>
                    </div>
                )}
                {action.tool_name === "write_to_scratchpad" && action.tool_input && typeof action.tool_input.note === "string" && (
                    <span className="agent-feed-scratchpad-note">
                        📌 {action.tool_input.note}
                    </span>
                )}

                {!expanded && action.tool_input && action.tool_name !== "write_to_scratchpad" && (
                    <span className="agent-feed-input-inline">
                        {formatToolInput(action.tool_input).slice(0, 100)}
                    </span>
                )}

                {expanded && (
                    <div className="agent-feed-details">
                        {action.tool_input && Object.keys(action.tool_input).length > 0 && (
                            <div className="agent-feed-detail-block">
                                <span className="agent-feed-detail-label">Input</span>
                                <pre className="agent-feed-detail-pre">
                                    {JSON.stringify(action.tool_input, null, 2)}
                                </pre>
                            </div>
                        )}
                        {action.result_preview && (
                            <div className="agent-feed-detail-block">
                                <span className="agent-feed-detail-label">Result</span>
                                <pre className="agent-feed-detail-pre">
                                    {action.result_preview}
                                </pre>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

export default function AgentActionFeed({
    actions,
    isActive,
    onStop,
    scratchpad = "",
}: AgentActionFeedProps) {
    const bottomRef = useRef<HTMLDivElement>(null);
    const listRef = useRef<HTMLDivElement>(null);
    // Collapse by default when research is complete, expand when active
    const [isExpanded, setIsExpanded] = useState(isActive);
    // Track if user has scrolled up (to disable auto-scroll)
    const [userScrolledUp, setUserScrolledUp] = useState(false);

    // Update expanded state when isActive changes
    useEffect(() => {
        if (isActive && !isExpanded) {
            setIsExpanded(true);
        }
    }, [isActive]);

    // Detect scroll position - disable auto-scroll if user scrolled up
    useEffect(() => {
        const list = listRef.current;
        if (!list) return;

        const handleScroll = () => {
            const { scrollTop, scrollHeight, clientHeight } = list;
            const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
            setUserScrolledUp(!isNearBottom);
        };

        list.addEventListener("scroll", handleScroll);
        return () => list.removeEventListener("scroll", handleScroll);
    }, []);

    // Smart auto-scroll: only scroll if user is near bottom
    useEffect(() => {
        if (isExpanded && !userScrolledUp) {
            bottomRef.current?.scrollIntoView({ behavior: "smooth" });
        }
    }, [actions.length, isExpanded, userScrolledUp]);

    if (actions.length === 0 && !isActive) return null;

    const toggleExpanded = () => setIsExpanded(!isExpanded);

    return (
        <div className={`agent-feed-card ${!isExpanded ? "collapsed" : ""}`}>
            {/* Header with toggle */}
            <div className="agent-feed-header">
                <div className="agent-feed-header-left">
                    <button
                        type="button"
                        className="agent-feed-toggle"
                        onClick={toggleExpanded}
                        aria-expanded={isExpanded}
                        title={isExpanded ? "Collapse" : "Expand"}
                    >
                        <span className={`agent-feed-toggle-icon ${isExpanded ? "expanded" : ""}`}>
                            {isExpanded ? "▾" : "▸"}
                        </span>
                        <span className="agent-feed-title">
                            {isActive ? "Research in Progress" : "Research Complete"}
                        </span>
                    </button>
                    {isActive && <span className="agent-feed-pulse" />}
                </div>

                <div className="agent-feed-header-right">
                    {!isActive && actions.length > 0 && (
                        <span className="agent-feed-badge agent-feed-badge--count">
                            {actions.length} actions
                        </span>
                    )}
                    {isActive && onStop && (
                        <button
                            type="button"
                            className="agent-feed-stop-btn"
                            onClick={onStop}
                            title="Stop the agent and cancel planning"
                        >
                            ⏹ Stop
                        </button>
                    )}
                </div>
            </div>

            {/* Collapsible content */}
            <div className={`agent-feed-collapsible ${isExpanded ? "expanded" : ""}`}>
                {/* Sticky Research Progress Tracker */}
                <div className="agent-feed-progress-sticky">
                    <ResearchProgressTracker scratchpad={scratchpad} isActive={isActive} />
                </div>

                {/* Action items list - scrollable */}
                <div className="agent-feed-list" ref={listRef}>
                    {actions.map((action, index) => (
                        <ActionItem key={index} action={action} />
                    ))}
                    {isActive && actions.length === 0 && (
                        <div className="agent-feed-item agent-feed-item--thinking">
                            <span className="agent-feed-icon">🔥</span>
                            <span className="agent-feed-summary agent-feed-thinking">
                                Warming up Nova 2 Lite…
                            </span>
                        </div>
                    )}
                    {isActive && actions.length > 0 && (
                        <div className="agent-feed-item agent-feed-item--thinking">
                            <span className="agent-feed-icon">💭</span>
                            <span className="agent-feed-summary agent-feed-thinking">Thinking…</span>
                        </div>
                    )}
                    <div ref={bottomRef} />
                </div>
            </div>
        </div>
    );
}
