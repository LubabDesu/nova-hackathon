// NovaSync — Research Progress Tracker (Animated Edition)
// Shows real-time checklist completion based on agent scratchpad notes

import { useMemo, useEffect, useState } from "react";

interface ResearchTask {
    id: string;
    text: string;
    checked: boolean;
    isNew?: boolean;
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
                isNew: false,
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

// Animated checkmark SVG component
function CheckmarkIcon({ checked }: { checked: boolean }) {
    return (
        <svg 
            width="16" 
            height="16" 
            viewBox="0 0 16 16" 
            style={{
                transition: "all 0.3s ease",
                transform: checked ? "scale(1.1)" : "scale(1)",
            }}
        >
            {checked ? (
                <>
                    <circle cx="8" cy="8" r="7" fill="none" stroke="#16a34a" strokeWidth="1.5" />
                    <path
                        d="M4.5 8 L7 10.5 L11.5 5.5"
                        fill="none"
                        stroke="#16a34a"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        style={{
                            strokeDasharray: 20,
                            strokeDashoffset: 0,
                            animation: "agent-check-stroke 0.3s ease forwards",
                        }}
                    />
                </>
            ) : (
                <circle cx="8" cy="8" r="7" fill="none" stroke="#cbd5e1" strokeWidth="1.5" />
            )}
        </svg>
    );
}

export default function ResearchProgressTracker({
    scratchpad,
    isActive,
}: ResearchProgressTrackerProps) {
    const [prevTasks, setPrevTasks] = useState<ResearchTask[]>([]);
    const tasks = useMemo(() => parseChecklist(scratchpad), [scratchpad]);
    const summary = useMemo(() => getProgressSummary(tasks), [tasks]);

    // Track new/changed tasks for animation
    useEffect(() => {
        setPrevTasks(tasks);
    }, [tasks]);

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

            {/* Progress bar with animated fill */}
            <div className="research-progress-bar-container">
                <div
                    className="research-progress-bar"
                    style={{ 
                        width: `${progressPercent}%`,
                        transition: "width 0.5s cubic-bezier(0.22, 1, 0.36, 1)",
                    }}
                />
            </div>

            {/* Task list */}
            <div className="research-task-list">
                {tasks.map((task, index) => {
                    const prevTask = prevTasks.find(t => t.id === task.id);
                    const justCompleted = task.checked && prevTask && !prevTask.checked;
                    
                    return (
                        <div
                            key={task.id}
                            className={`research-task ${task.checked ? "checked" : "unchecked"}`}
                            style={{
                                animationDelay: `${index * 0.05}s`,
                                ...(justCompleted ? {
                                    animation: "agent-bounce-subtle 0.4s ease",
                                } : {}),
                            }}
                        >
                            <span 
                                className="research-task-checkbox"
                                style={{
                                    display: "inline-flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                    transition: "transform 0.2s ease",
                                }}
                            >
                                <CheckmarkIcon checked={task.checked} />
                            </span>
                            <span 
                                className="research-task-text"
                                style={{
                                    transition: "all 0.3s ease",
                                    textDecoration: task.checked ? "line-through" : "none",
                                    opacity: task.checked ? 0.7 : 1,
                                    color: task.checked ? "#16a34a" : "#1e3a5f",
                                }}
                            >
                                {task.text}
                            </span>
                        </div>
                    );
                })}
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
