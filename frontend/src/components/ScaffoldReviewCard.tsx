// NovaSync — Human-in-the-loop scaffold review card (Animated Edition)

import { useState, useEffect, type ReactNode } from "react";

interface ScaffoldReviewCardProps {
    scaffoldText: string;
    revisionCount: number;
    maxRevisions: number;
    feedback: string;
    onFeedbackChange: (value: string) => void;
    onRevise: () => void;
    onApprove: (approvedScaffold: string) => void;
    isRevising: boolean;
    isExtracting?: boolean;
    scratchpad?: string;
}

function findDaySeparatorColon(line: string): number {
    // Find the colon AFTER the last closing bracket (avoids [Weather: 12°C] false matches)
    const lastBracket = line.lastIndexOf("]");
    if (lastBracket > -1) {
        const after = line.indexOf(":", lastBracket);
        if (after > -1) return after;
    }
    return line.indexOf(":");
}

// Animated day block component
function DayBlock({ 
    dayLabel, 
    activities, 
    index 
}: { 
    dayLabel: string; 
    activities: string[]; 
    index: number;
}) {
    const [isVisible, setIsVisible] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => setIsVisible(true), index * 100);
        return () => clearTimeout(timer);
    }, [index]);

    return (
        <div 
            className="scaffold-day-block"
            style={{
                opacity: isVisible ? 1 : 0,
                transform: isVisible ? 'translateY(0)' : 'translateY(15px)',
                transition: 'all 0.4s cubic-bezier(0.22, 1, 0.36, 1)',
                transitionDelay: `${index * 0.1}s`,
            }}
        >
            <div className="scaffold-day-header">{dayLabel}</div>
            {activities.length > 0 && (
                <div className="scaffold-activity-list">
                    {activities.map((activity, ai) => (
                        <div 
                            key={ai} 
                            className="scaffold-activity-item"
                            style={{
                                opacity: isVisible ? 1 : 0,
                                transform: isVisible ? 'translateX(0)' : 'translateX(-10px)',
                                transition: 'all 0.3s ease',
                                transitionDelay: `${(index * 0.1) + (ai * 0.05)}s`,
                            }}
                        >
                            <span className="scaffold-activity-dot">•</span>
                            <span className="scaffold-activity-text">{activity}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

function renderScaffoldDays(text: string): ReactNode[] {
    const lines = text.split("\n");
    const days: ReactNode[] = [];
    let currentDayLines: string[] = [];
    let currentDayIndex = 0;

    const flushDay = () => {
        if (currentDayLines.length === 0) return;
        
        const dayLine = currentDayLines[0];
        const colonIdx = findDaySeparatorColon(dayLine);
        const dayLabel = colonIdx > -1 ? dayLine.slice(0, colonIdx + 1) : dayLine;
        const activitiesText = colonIdx > -1 ? dayLine.slice(colonIdx + 1).trim() : "";
        
        // Parse activities from the first line (split by bullet)
        const activityParts = activitiesText.split("·").map((a) => a.trim()).filter(Boolean);
        
        // Collect additional activities from subsequent lines that start with "·" or are activity-like
        const additionalActivities: string[] = [];
        for (let i = 1; i < currentDayLines.length; i++) {
            const line = currentDayLines[i].trim();
            // Skip empty lines and section headers
            if (!line || /^(Trip overview|Notes|Open questions):/i.test(line)) {
                continue;
            }
            // If line looks like a day header, stop collecting
            if (/^Day\s+\d+/i.test(line)) {
                break;
            }
            // Remove leading bullet if present
            const cleanLine = line.replace(/^[·•]\s*/, "").trim();
            if (cleanLine) {
                additionalActivities.push(cleanLine);
            }
        }
        
        const allActivities = [...activityParts, ...additionalActivities];
        
        days.push(
            <DayBlock 
                key={currentDayIndex}
                dayLabel={dayLabel}
                activities={allActivities}
                index={currentDayIndex}
            />
        );
        currentDayIndex++;
        currentDayLines = [];
    };

    let inDay = false;
    
    for (const line of lines) {
        const trimmed = line.trim();
        
        // Check if this is a day header
        if (/^Day\s+\d+/i.test(trimmed)) {
            if (inDay) {
                flushDay();
            }
            currentDayLines = [trimmed];
            inDay = true;
            continue;
        }
        
        // Check for section headers (end of days section)
        if (/^(Trip overview|Notes|Open questions):/i.test(trimmed)) {
            if (inDay) {
                flushDay();
                inDay = false;
            }
            days.push(
                <div key={`section-${days.length}`} className="scaffold-section-header">
                    {trimmed}
                </div>
            );
            continue;
        }
        
        // Empty line ends current day collection
        if (!trimmed && inDay) {
            flushDay();
            inDay = false;
            continue;
        }
        
        // Collect lines within a day
        if (inDay) {
            currentDayLines.push(line);
        } else if (trimmed) {
            // Non-day content
            days.push(
                <div key={`line-${days.length}`} className="scaffold-body-line">
                    {trimmed}
                </div>
            );
        }
    }
    
    // Flush any remaining day
    if (inDay && currentDayLines.length > 0) {
        flushDay();
    }
    
    return days;
}

// Animated checkmark for notes
function NoteCheckmark({ checked }: { checked: boolean }) {
    return (
        <span style={{ 
            display: "inline-flex",
            alignItems: "center",
            marginRight: 6,
            color: checked ? "#16a34a" : "#cbd5e1",
            transition: "all 0.3s ease",
        }}>
            {checked ? "☑" : "☐"}
        </span>
    );
}

export default function ScaffoldReviewCard({
    scaffoldText,
    revisionCount,
    maxRevisions,
    feedback,
    onFeedbackChange,
    onRevise,
    onApprove,
    isRevising,
    isExtracting = false,
    scratchpad,
}: ScaffoldReviewCardProps) {
    const canRevise = revisionCount < maxRevisions;
    const [showNotes, setShowNotes] = useState(false);
    const [isVisible, setIsVisible] = useState(false);

    // Entrance animation
    useEffect(() => {
        const timer = setTimeout(() => setIsVisible(true), 100);
        return () => clearTimeout(timer);
    }, []);

    return (
        <div 
            className="scaffold-review-card"
            style={{
                opacity: isVisible ? 1 : 0,
                transform: isVisible ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.98)',
                transition: 'all 0.5s cubic-bezier(0.22, 1, 0.36, 1)',
            }}
        >
            <div className="scaffold-review-header">
                <span className="scaffold-review-icon">✦</span>
                <span className="scaffold-review-title">
                    Here&apos;s your draft plan — does this look right?
                </span>
            </div>

            <div className="scaffold-review-body">
                {renderScaffoldDays(scaffoldText)}
            </div>

            {scratchpad && scratchpad.trim() && (
                <div 
                    className="scaffold-agent-notes"
                    style={{
                        opacity: isVisible ? 1 : 0,
                        transition: 'opacity 0.4s ease 0.3s',
                    }}
                >
                    <button
                        type="button"
                        className="scaffold-notes-toggle"
                        onClick={() => setShowNotes(!showNotes)}
                    >
                        <span className="scaffold-notes-icon">📝</span>
                        <span className="scaffold-notes-label">Nova&apos;s research notes</span>
                        <span 
                            className="scaffold-notes-chevron"
                            style={{
                                transform: showNotes ? 'rotate(90deg)' : 'rotate(0deg)',
                                transition: 'transform 0.3s ease',
                            }}
                        >
                            ▸
                        </span>
                    </button>
                    {showNotes && (
                        <div 
                            className="scaffold-notes-content"
                            style={{
                                animation: 'agent-slide-in 0.3s ease',
                            }}
                        >
                            {scratchpad.split("\n").map((line, i) => {
                                const trimmed = line.trim();
                                const isChecked = /^\[x\]/i.test(trimmed);
                                const isUnchecked = /^\[\s\]/.test(trimmed);
                                const isNote = trimmed.toLowerCase().startsWith('note:');
                                const isResult = trimmed.toLowerCase().startsWith('result');
                                
                                let lineClass = "scaffold-note-line";
                                if (isChecked) lineClass += " scaffold-note-checked";
                                else if (isUnchecked) lineClass += " scaffold-note-unchecked";
                                else if (isNote) lineClass += " scaffold-note-highlight";
                                else if (isResult) lineClass += " scaffold-note-result";
                                else if (!trimmed) lineClass += " scaffold-note-empty";
                                
                                return (
                                    <div key={i} className={lineClass}>
                                        {(isChecked || isUnchecked) && (
                                            <NoteCheckmark checked={isChecked} />
                                        )}
                                        {line || "\u00A0"}
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}

            <div 
                className="scaffold-review-footer"
                style={{
                    opacity: isVisible ? 1 : 0,
                    transition: 'opacity 0.4s ease 0.4s',
                }}
            >
                <textarea
                    className="scaffold-feedback-input"
                    placeholder="Anything you'd like to change? (optional)"
                    value={feedback}
                    onChange={(e) => onFeedbackChange(e.target.value)}
                    rows={2}
                    disabled={isRevising || !canRevise}
                />
                <div className="scaffold-review-actions">
                    <span className="scaffold-revision-counter">
                        Revision {revisionCount}/{maxRevisions}
                    </span>
                    <div className="scaffold-review-buttons">
                        <button
                            type="button"
                            className="scaffold-btn scaffold-btn-revise"
                            onClick={onRevise}
                            disabled={
                                !canRevise ||
                                isRevising ||
                                feedback.trim().length === 0
                            }
                            title={
                                !canRevise
                                    ? "Max revisions reached"
                                    : "Revise the plan with your feedback"
                            }
                        >
                            {isRevising ? (
                                <>
                                    <span className="spinner-inline" /> Revising…
                                </>
                            ) : canRevise ? (
                                "Revise plan"
                            ) : (
                                "Max revisions reached"
                            )}
                        </button>
                        <button
                            type="button"
                            className="scaffold-btn scaffold-btn-approve"
                            onClick={() => onApprove(scaffoldText)}
                            disabled={isRevising || isExtracting}
                        >
                            Approve &amp; Generate
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
