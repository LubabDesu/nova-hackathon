// NovaSync — Zone 1: Idea Input (Animated Edition)

import { useState, useEffect } from "react";

interface IdeaInputProps {
    onSubmit: (idea: string) => void;
    loading: boolean;
    loadingPhaseLabel?: string;
    loadingPhaseDetail?: string;
    loadingStepIndex?: number;
    loadingTotalSteps?: number;
}

// Animated dots for loading states
function LoadingDots() {
    return (
        <span className="loading-dots">
            <span />
            <span />
            <span />
        </span>
    );
}

export default function IdeaInput({
    onSubmit,
    loading,
    loadingPhaseLabel,
    loadingPhaseDetail,
    loadingStepIndex,
    loadingTotalSteps,
}: IdeaInputProps) {
    const [idea, setIdea] = useState("");
    const [progressPercent, setProgressPercent] = useState(0);

    const handleSubmit = () => {
        if (idea.trim().length === 0) return;
        onSubmit(idea.trim());
    };

    // Animate progress bar based on step index
    useEffect(() => {
        if (typeof loadingStepIndex === "number" && typeof loadingTotalSteps === "number" && loadingTotalSteps > 0) {
            const targetPercent = ((loadingStepIndex + 1) / loadingTotalSteps) * 100;
            setProgressPercent(targetPercent);
        }
    }, [loadingStepIndex, loadingTotalSteps]);

    return (
        <div className="idea-input-zone">
            <h2 className="zone-title">
                <span className="zone-icon">✨</span> Drop Your Travel Ideas
            </h2>
            <p className="zone-subtitle">
                What else do you want the model to know?
            </p>
            <textarea
                className="idea-textarea"
                value={idea}
                onChange={(e) => setIdea(e.target.value)}
                placeholder={`e.g. "I want to hike Cradle Mountain for a day, visit MONA art museum in Hobart, take the ferry to Bruny Island for seafood, and do a wine tasting at Coal River Valley..."`}
                rows={8}
                disabled={loading}
            />
            <button
                className={`submit-btn ${loading ? "loading" : ""}`}
                onClick={handleSubmit}
                disabled={loading || idea.trim().length === 0}
            >
                {loading ? (
                    <>
                        <span className="spinner" /> Processing<LoadingDots />
                    </>
                ) : (
                    "Extract Itinerary →"
                )}
            </button>
            {loading && (
                <div className="planning-progress-card">
                    <div className="planning-progress-header">
                        <p className="planning-progress-kicker">
                            Planner status
                            {typeof loadingStepIndex === "number" &&
                            typeof loadingTotalSteps === "number"
                                ? ` • Step ${loadingStepIndex + 1} of ${loadingTotalSteps}`
                                : ""}
                        </p>
                        <div className="planning-progress-pulse" />
                    </div>
                    <p className="planning-progress-title">
                        {loadingPhaseLabel ?? "Processing request"}
                        <LoadingDots />
                    </p>
                    <p className="planning-progress-detail">
                        {loadingPhaseDetail ??
                            "Running trip-planning pipeline..."}
                    </p>
                    {/* Progress bar */}
                    {typeof loadingStepIndex === "number" && typeof loadingTotalSteps === "number" && (
                        <div className="planning-progress-bar-container">
                            <div 
                                className="planning-progress-bar"
                                style={{ width: `${progressPercent}%` }}
                            />
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
