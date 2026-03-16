// NovaSync — Agent question card (Animated Edition)
// Renders preset option buttons + a freeform "Other" textarea.
// After answering, collapses to a small summary row.

import { useState, useEffect, useRef } from "react";
import type { AgentQuestionEvent } from "../types";

interface AgentQuestionCardProps {
    question: AgentQuestionEvent;
    onAnswer: (answer: string) => Promise<void>;
    isSubmitting: boolean;
}

export default function AgentQuestionCard({
    question,
    onAnswer,
    isSubmitting,
}: AgentQuestionCardProps) {
    const [answered, setAnswered] = useState<{ answer: string } | null>(null);
    const [customText, setCustomText] = useState("");
    const [isVisible, setIsVisible] = useState(false);
    const cardRef = useRef<HTMLDivElement>(null);

    const disabled = isSubmitting;

    // Trigger entrance animation
    useEffect(() => {
        const timer = setTimeout(() => setIsVisible(true), 50);
        return () => clearTimeout(timer);
    }, []);

    // Scroll into view when question appears
    useEffect(() => {
        if (isVisible && cardRef.current) {
            cardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }, [isVisible]);

    const handlePreset = (label: string) => {
        if (disabled) return;
        setAnswered({ answer: label });
        onAnswer(label);
    };

    const handleCustomSubmit = () => {
        const text = customText.trim();
        if (!text || disabled) return;
        setAnswered({ answer: text });
        onAnswer(text);
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            handleCustomSubmit();
        }
    };

    // ── Collapsed state ──────────────────────────────────────────────────
    if (answered) {
        return (
            <div className="agent-question-answered" style={styles.collapsed}>
                <span style={styles.collapsedText}>
                    <span style={styles.checkmark}>✓</span>
                    <span style={styles.collapsedQuestion}>{question.question}</span>
                    <span style={styles.arrow}>→</span>
                    <span style={styles.collapsedAnswer}>{answered.answer}</span>
                </span>
            </div>
        );
    }

    // ── Expanded state ───────────────────────────────────────────────────
    return (
        <div 
            ref={cardRef}
            className={`agent-question-wrapper ${isVisible ? 'active' : ''}`}
            style={{
                ...styles.card,
                opacity: isVisible ? 1 : 0,
                transform: isVisible ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.96)',
            }}
        >
            <div style={styles.headerLabel}>
                <span style={styles.pulseIcon}>💬</span>
                Nova has a question
            </div>

            <div style={styles.questionText}>
                &ldquo;{question.question}&rdquo;
            </div>

            {question.options.length > 0 && (
                <div style={styles.optionsRow}>
                    {question.options.map((opt, idx) => (
                        <button
                            key={opt.id}
                            type="button"
                            className="agent-option-btn"
                            style={{
                                ...styles.optionBtn,
                                ...(disabled ? styles.optionBtnDisabled : {}),
                                animationDelay: `${idx * 0.05}s`,
                            }}
                            disabled={disabled}
                            onClick={() => handlePreset(opt.label)}
                        >
                            {opt.label}
                        </button>
                    ))}
                </div>
            )}

            <div style={styles.divider}>
                <span style={styles.dividerText}>or type your own</span>
            </div>

            <div style={styles.otherRow}>
                <textarea
                    style={{
                        ...styles.textarea,
                        ...(disabled ? { opacity: 0.45 } : {}),
                    }}
                    value={customText}
                    onChange={(e) => setCustomText(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Type a custom answer... (Cmd+Enter to submit)"
                    disabled={disabled}
                    rows={2}
                />
                <button
                    type="button"
                    className="agent-submit-btn"
                    style={{
                        ...styles.submitBtn,
                        ...(disabled || !customText.trim()
                            ? styles.submitBtnDisabled
                            : {}),
                    }}
                    disabled={disabled || !customText.trim()}
                    onClick={handleCustomSubmit}
                >
                    {isSubmitting ? (
                        <span style={styles.spinner}>◌</span>
                    ) : (
                        "Submit"
                    )}
                </button>
            </div>
        </div>
    );
}

const styles: Record<string, React.CSSProperties> = {
    card: {
        background: "linear-gradient(135deg, rgba(255,255,255,0.9) 0%, rgba(255,255,255,0.75) 100%)",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        border: "1px solid rgba(255,255,255,0.95)",
        borderRadius: 20,
        borderLeft: "4px solid #4A90D9",
        padding: "24px",
        boxShadow: "0 4px 24px rgba(74, 141, 196, 0.15), 0 1px 3px rgba(0,0,0,0.05)",
        transition: "all 0.5s cubic-bezier(0.22, 1, 0.36, 1)",
    },

    collapsed: {
        background: "linear-gradient(135deg, rgba(255,255,255,0.7) 0%, rgba(255,255,255,0.5) 100%)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        border: "1px solid rgba(255,255,255,0.8)",
        borderRadius: 12,
        borderLeft: "3px solid #16a34a",
        padding: "12px 18px",
        boxShadow: "0 2px 12px rgba(22, 163, 74, 0.1)",
        transition: "all 0.3s ease",
    },

    headerLabel: {
        fontFamily: "'DM Sans', sans-serif",
        fontSize: "0.75rem",
        fontWeight: 600,
        color: "#4A90D9",
        textTransform: "uppercase" as const,
        letterSpacing: "0.12em",
        marginBottom: 14,
        display: "flex",
        alignItems: "center",
        gap: 8,
    },

    pulseIcon: {
        animation: "agent-bounce-subtle 2s ease-in-out infinite",
    },

    questionText: {
        fontFamily: "'Cormorant Garamond', serif",
        fontSize: "1.25rem",
        fontStyle: "italic",
        fontWeight: 500,
        color: "#0b1f38",
        lineHeight: 1.5,
        marginBottom: 20,
    },

    optionsRow: {
        display: "flex",
        flexWrap: "wrap" as const,
        gap: 10,
        marginBottom: 16,
    },

    optionBtn: {
        background: "rgba(74, 144, 217, 0.08)",
        border: "1px solid rgba(74, 144, 217, 0.3)",
        borderRadius: 10,
        padding: "10px 20px",
        color: "#1e3a5f",
        fontFamily: "'DM Sans', sans-serif",
        fontSize: "0.9rem",
        fontWeight: 500,
        cursor: "pointer",
        transition: "all 0.2s cubic-bezier(0.22, 1, 0.36, 1)",
        animation: "agent-fade-in 0.4s ease forwards",
        opacity: 0,
    },

    optionBtnDisabled: {
        opacity: 0.45,
        cursor: "not-allowed",
    },

    divider: {
        display: "flex",
        alignItems: "center",
        margin: "12px 0 16px",
        gap: 12,
    },

    dividerText: {
        fontFamily: "'DM Sans', sans-serif",
        fontSize: "0.75rem",
        color: "#8ba5c0",
        fontStyle: "italic",
    },

    otherRow: {
        display: "flex",
        gap: 12,
        alignItems: "flex-start",
    },

    textarea: {
        flex: 1,
        background: "rgba(255,255,255,0.9)",
        border: "1px solid rgba(74, 144, 217, 0.25)",
        borderRadius: 10,
        padding: "12px 14px",
        color: "#0b1f38",
        fontFamily: "'DM Sans', sans-serif",
        fontSize: "0.9rem",
        resize: "vertical" as const,
        minHeight: 48,
        outline: "none",
        transition: "all 0.2s ease",
        boxShadow: "0 1px 2px rgba(0,0,0,0.02)",
    },

    submitBtn: {
        background: "linear-gradient(135deg, #4A90D9 0%, #3a7bc8 100%)",
        color: "white",
        border: "none",
        borderRadius: 10,
        padding: "12px 24px",
        fontFamily: "'DM Sans', sans-serif",
        fontSize: "0.9rem",
        fontWeight: 600,
        cursor: "pointer",
        transition: "all 0.2s ease",
        whiteSpace: "nowrap" as const,
        boxShadow: "0 2px 8px rgba(74, 144, 217, 0.3)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 90,
    },

    submitBtnDisabled: {
        opacity: 0.45,
        cursor: "not-allowed",
        boxShadow: "none",
    },

    spinner: {
        display: "inline-block",
        animation: "agent-spin-slow 1s linear infinite",
        fontSize: "1.1rem",
    },

    collapsedText: {
        fontFamily: "'DM Sans', sans-serif",
        fontSize: "0.85rem",
        color: "#1e3a5f",
        lineHeight: 1.5,
        display: "flex",
        alignItems: "center",
        gap: 10,
        flexWrap: "wrap" as const,
    },

    checkmark: {
        color: "#16a34a",
        fontWeight: 700,
        fontSize: "1rem",
    },

    collapsedQuestion: {
        color: "#4a6a8a",
        fontStyle: "italic",
    },

    arrow: {
        color: "#8ba5c0",
    },

    collapsedAnswer: {
        color: "#0b1f38",
        fontWeight: 600,
    },
};
