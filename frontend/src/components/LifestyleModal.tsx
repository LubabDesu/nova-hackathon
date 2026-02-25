// NovaSync — Lifestyle onboarding modal (7-step questionnaire)

import { useState } from "react";
import type { LifestyleProfile } from "../types";

interface LifestyleModalProps {
    onConfirm: (profile: LifestyleProfile) => void;
    onSkip: () => void;
}

const STORAGE_KEY = "nova_lifestyle_profile";

interface Question {
    id: keyof LifestyleProfile;
    label: string;
    multi: boolean;
    options: { value: string; label: string }[];
}

const QUESTIONS: Question[] = [
    {
        id: "wake_time_pref",
        label: "What's your wake-up style?",
        multi: false,
        options: [
            { value: "early_bird", label: "Early bird (up by 7am)" },
            { value: "standard", label: "Standard (up by 9am)" },
            { value: "late_riser", label: "Late riser (up after 10am)" },
        ],
    },
    {
        id: "travel_party",
        label: "Who are you travelling with?",
        multi: true,
        options: [
            { value: "solo", label: "Solo" },
            { value: "partner", label: "Partner" },
            { value: "family_young_kids", label: "Family (young kids)" },
            { value: "friends", label: "Friends" },
            { value: "elderly", label: "Elderly companions" },
        ],
    },
    {
        id: "dietary",
        label: "Any dietary needs?",
        multi: true,
        options: [
            { value: "none", label: "None" },
            { value: "vegetarian", label: "Vegetarian" },
            { value: "vegan", label: "Vegan" },
            { value: "halal", label: "Halal" },
            { value: "kosher", label: "Kosher" },
            { value: "gluten_free", label: "Gluten-free" },
        ],
    },
    {
        id: "fitness_level",
        label: "How active do you like to be?",
        multi: false,
        options: [
            { value: "low", label: "Low — easy strolls only" },
            { value: "moderate", label: "Moderate — some walking" },
            { value: "high", label: "High — bring on the hikes!" },
        ],
    },
    {
        id: "accommodation_style",
        label: "Where do you like to stay?",
        multi: false,
        options: [
            { value: "budget", label: "Budget / hostels" },
            { value: "mid_range", label: "Mid-range hotels" },
            { value: "boutique", label: "Boutique / design hotels" },
            { value: "luxury", label: "Luxury" },
        ],
    },
    {
        id: "pace",
        label: "What's your trip pace?",
        multi: false,
        options: [
            { value: "relaxed", label: "Relaxed — slow travel" },
            { value: "moderate", label: "Moderate — balanced" },
            { value: "packed", label: "Packed — see everything!" },
        ],
    },
    {
        id: "mobility_mode",
        label: "Any mobility considerations?",
        multi: false,
        options: [
            { value: "none", label: "None" },
            { value: "prefer_flat", label: "Prefer flat / minimal steps" },
            { value: "wheelchair", label: "Wheelchair accessible required" },
        ],
    },
];

function emptyProfile(): LifestyleProfile {
    return {
        wake_time_pref: null,
        travel_party: [],
        dietary: [],
        fitness_level: null,
        accommodation_style: null,
        pace: null,
        mobility_mode: null,
    };
}

export function saveLifestyleProfile(profile: LifestyleProfile) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
    } catch {
        // storage not available
    }
}

export function loadLifestyleProfile(): LifestyleProfile | null {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        return JSON.parse(raw) as LifestyleProfile;
    } catch {
        return null;
    }
}

export default function LifestyleModal({ onConfirm, onSkip }: LifestyleModalProps) {
    const [step, setStep] = useState(0);
    const [profile, setProfile] = useState<LifestyleProfile>(emptyProfile);

    const question = QUESTIONS[step];
    const totalSteps = QUESTIONS.length;
    const isLastStep = step === totalSteps - 1;

    const currentValue = (profile[question.id] as string | string[] | null);

    function toggleOption(value: string) {
        const qid = question.id;
        if (question.multi) {
            const current = (profile[qid] as string[]) ?? [];
            const next = current.includes(value)
                ? current.filter((v) => v !== value)
                : [...current, value];
            setProfile((prev) => ({ ...prev, [qid]: next }));
        } else {
            setProfile((prev) => ({ ...prev, [qid]: value }));
        }
    }

    function isSelected(value: string): boolean {
        if (question.multi) {
            return ((profile[question.id] as string[]) ?? []).includes(value);
        }
        return profile[question.id] === value;
    }

    function handleNext() {
        if (isLastStep) {
            saveLifestyleProfile(profile);
            onConfirm(profile);
        } else {
            setStep((s) => s + 1);
        }
    }

    function handleBack() {
        if (step > 0) setStep((s) => s - 1);
    }

    const canAdvance = question.multi
        ? true  // multi-select always has a "skip this" via empty selection
        : profile[question.id] !== null && profile[question.id] !== "";

    return (
        <div className="lifestyle-modal-overlay">
            <div className="lifestyle-modal">
                <div className="lifestyle-modal-header">
                    <span className="lifestyle-modal-title">Tell us about you</span>
                    <span className="lifestyle-modal-step">
                        {step + 1} / {totalSteps}
                    </span>
                </div>

                <div className="lifestyle-modal-progress">
                    <div
                        className="lifestyle-modal-progress-bar"
                        style={{ width: `${((step + 1) / totalSteps) * 100}%` }}
                    />
                </div>

                <h2 className="lifestyle-modal-question">{question.label}</h2>

                <div className={`lifestyle-modal-options ${question.multi ? "is-multi" : ""}`}>
                    {question.options.map((opt) => (
                        <button
                            key={opt.value}
                            type="button"
                            className={`lifestyle-option-pill ${isSelected(opt.value) ? "is-selected" : ""}`}
                            onClick={() => toggleOption(opt.value)}
                        >
                            {opt.label}
                        </button>
                    ))}
                </div>

                {question.multi && (
                    <p className="lifestyle-modal-hint">Select all that apply</p>
                )}

                <div className="lifestyle-modal-actions">
                    {step > 0 && (
                        <button
                            type="button"
                            className="lifestyle-btn lifestyle-btn--back"
                            onClick={handleBack}
                        >
                            Back
                        </button>
                    )}
                    <button
                        type="button"
                        className="lifestyle-btn lifestyle-btn--skip"
                        onClick={onSkip}
                    >
                        Skip for now
                    </button>
                    <button
                        type="button"
                        className={`lifestyle-btn lifestyle-btn--next ${!canAdvance && !question.multi ? "is-disabled" : ""}`}
                        onClick={handleNext}
                        disabled={!canAdvance && !question.multi}
                    >
                        {isLastStep ? "Start planning" : "Next"}
                    </button>
                </div>
            </div>
        </div>
    );
}
