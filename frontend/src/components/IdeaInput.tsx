// NovaSync — Zone 1: Idea Input

import { useState } from "react";

interface IdeaInputProps {
    onSubmit: (idea: string) => void;
    loading: boolean;
    loadingPhaseLabel?: string;
    loadingPhaseDetail?: string;
    loadingStepIndex?: number;
    loadingTotalSteps?: number;
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

    const handleSubmit = () => {
        if (idea.trim().length === 0) return;
        onSubmit(idea.trim());
    };

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
                className="submit-btn"
                onClick={handleSubmit}
                disabled={loading || idea.trim().length === 0}
            >
                {loading ? (
                    <>
                        <span className="spinner" /> Processing…
                    </>
                ) : (
                    "Extract Itinerary →"
                )}
            </button>
            {loading && (
                <div className="planning-progress-card">
                    <p className="planning-progress-kicker">
                        Planner status
                        {typeof loadingStepIndex === "number" &&
                        typeof loadingTotalSteps === "number"
                            ? ` • Step ${loadingStepIndex + 1} of ${loadingTotalSteps}`
                            : ""}
                    </p>
                    <p className="planning-progress-title">
                        {loadingPhaseLabel ?? "Processing request"}
                    </p>
                    <p className="planning-progress-detail">
                        {loadingPhaseDetail ??
                            "Running trip-planning pipeline..."}
                    </p>
                </div>
            )}
        </div>
    );
}
