// NovaSync — Research Progress Tracker
// Shows real-time checklist completion based on agent scratchpad notes

import { useMemo } from "react";

interface ResearchTask {
    id: string;
    text: string;
    checked: boolean;
}

interface ResearchProgressTrackerProps {
    scratchpad: string;
    isActive: boolean;
}

function parseChecklist(scratchpad: string): ResearchTask[] {
    const lines = scratchpad.split("\n");
    // Use Map to deduplicate by task number, keeping latest
    const taskMap = new Map<string, ResearchTask>();

    for (let i = 0; i < lines.length; i++) {
        const trimmed = lines[i].trim();
        // Match patterns like "[ ] 1. Search for..." or "[x] 1. Search for..."
        // Also match "Note: [ ]" patterns
        const match = trimmed.match(/^(?:Note:\s*)?\[([ xX])\]\s*(.+)$/);
        if (match) {
            const text = match[2].trim();
            const checked = match[1].toLowerCase() === "x";
            
            // Extract task number if present (e.g., "1. Search..." -> "1")
            const numMatch = text.match(/^(\d+)\.\s*/);
            const taskKey = numMatch ? `num-${numMatch[1]}` : `line-${i}`;
            
            // Keep only the latest version of each numbered task
            taskMap.set(taskKey, {
                id: taskKey,
                text: text,
                checked: checked,
            });
        }
    }
    
    // Convert map to array, preserving original order as much as possible
    return Array.from(taskMap.values());
}

function getProgressSummary(tasks: ResearchTask[]): string {
    if (tasks.length === 0) return "";
    const completed = tasks.filter((t) => t.checked).length;
    return `${completed} of ${tasks.length} tasks completed`;
}

export default function ResearchProgressTracker({
    scratchpad,
    isActive,
}: ResearchProgressTrackerProps) {
    const tasks = useMemo(() => parseChecklist(scratchpad), [scratchpad]);
    const summary = useMemo(() => getProgressSummary(tasks), [tasks]);

    if (tasks.length === 0) return null;

    const completedCount = tasks.filter((t) => t.checked).length;
    const progressPercent = (completedCount / tasks.length) * 100;

    return (
        <div className="research-progress">
            <div className="research-progress-header">
                <span className="research-progress-icon">📋</span>
                <span className="research-progress-title">Research Tasks</span>
                <span className="research-progress-summary">{summary}</span>
            </div>

            {/* Progress bar */}
            <div className="research-progress-bar-container">
                <div
                    className="research-progress-bar"
                    style={{ width: `${progressPercent}%` }}
                />
            </div>

            {/* Task list */}
            <div className="research-task-list">
                {tasks.map((task) => (
                    <div
                        key={task.id}
                        className={`research-task ${task.checked ? "checked" : "unchecked"}`}
                    >
                        <span className="research-task-checkbox">
                            {task.checked ? "☑" : "☐"}
                        </span>
                        <span className="research-task-text">{task.text}</span>
                    </div>
                ))}
            </div>

            {isActive && (
                <div className="research-progress-status">
                    <span className="research-progress-pulse" />
                    <span>Research in progress...</span>
                </div>
            )}
        </div>
    );
}
