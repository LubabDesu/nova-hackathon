// NovaSync — main application shell (PlanPage)

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
    getCountries,
    getStatesOfCountry,
    type ICountry,
    type IState,
} from "@countrystatecity/countries";
import { useAuth } from "../contexts/AuthContext";
import IdeaInput from "../components/IdeaInput";
import ResultDisplay from "../components/ResultDisplay";
import LifestyleModal, {
    loadLifestyleProfile,
    saveLifestyleProfile,
} from "../components/LifestyleModal";
import { lifestyleToDirectives } from "../utils/lifestyleToDirectives";
import { cancelPlanningRequest, debugUrlScraper, planIdeaStream, processIdeaStream, reviseScaffold, extractIdeaStream } from "../services/api";
import type {
    InputDirectives,
    ItineraryNode,
    LifestyleProfile,
    ProcessIdeaDebugResponse,
    UrlScraperDebugResponse,
    AgentActionEvent,
    AgentQuestionEvent,
} from "../types";
import "../styles/sky-theme.css";
import "../styles/agent-animations.css";
import IdeaDropzone from "../components/IdeaDropZone";
import ScaffoldReviewCard from "../components/ScaffoldReviewCard";
import AgentActionFeed from "../components/AgentActionFeed";
import AgentQuestionCard from "../components/AgentQuestionCard";

const sortByName = <T extends { name: string }>(a: T, b: T) =>
    a.name.localeCompare(b.name);

const nodeIdentity = (node: ItineraryNode) =>
    [
        node.date_local ?? "",
        node.start_time_local ?? "",
        node.end_time_local ?? "",
        node.title,
        node.activity_type,
        node.description ?? "",
        node.segment_origin ?? "",
        node.segment_kind ?? "",
    ].join("|");

const mergeNodesByIdentity = (
    existing: ItineraryNode[],
    incoming: ItineraryNode[],
): ItineraryNode[] => {
    const map = new Map<string, ItineraryNode>();
    for (const node of existing) {
        map.set(nodeIdentity(node), node);
    }
    for (const node of incoming) {
        map.set(nodeIdentity(node), node);
    }
    return [...map.values()];
};

const asItineraryNodeArray = (value: unknown): ItineraryNode[] => {
    if (!Array.isArray(value)) return [];
    const nodes: ItineraryNode[] = [];
    for (const item of value) {
        if (!item || typeof item !== "object") continue;
        const record = item as Partial<ItineraryNode>;
        if (
            typeof record.title !== "string"
            || typeof record.activity_type !== "string"
        ) {
            continue;
        }
        nodes.push({
            title: record.title,
            activity_type: record.activity_type,
            duration_mins: typeof record.duration_mins === "number"
                ? record.duration_mins
                : null,
            date_local: typeof record.date_local === "string"
                ? record.date_local
                : null,
            start_time_local: typeof record.start_time_local === "string"
                ? record.start_time_local
                : null,
            end_time_local: typeof record.end_time_local === "string"
                ? record.end_time_local
                : null,
            lat: typeof record.lat === "number" ? record.lat : null,
            long: typeof record.long === "number" ? record.long : null,
            description: typeof record.description === "string"
                ? record.description
                : null,
            segment_origin:
                record.segment_origin === "model"
                || record.segment_origin === "synthetic"
                    ? record.segment_origin
                    : null,
            segment_kind:
                record.segment_kind === "activity"
                || record.segment_kind === "transfer"
                || record.segment_kind === "buffer"
                || record.segment_kind === "rest"
                    ? record.segment_kind
                    : null,
        });
    }
    return nodes;
};

interface LoadingPhase {
    stage: string;
    label: string;
    defaultDetail: string;
}

const LOADING_PHASES: LoadingPhase[] = [
    {
        stage: "prepare_media",
        label: "Curating evidence",
        defaultDetail: "Collecting signals from your text, links, and uploaded media.",
    },
    {
        stage: "create_trip",
        label: "Creating trip session",
        defaultDetail: "Preparing trip context and persistence session.",
    },
    {
        stage: "orchestrate_itinerary_planning",
        label: "Parsing and grounding data",
        defaultDetail: "Normalizing web facts and extracting scheduling constraints.",
    },
    {
        stage: "planner_critique",
        label: "Reviewing the draft plan",
        defaultDetail: "Checking timings, constraints, and completeness.",
    },
    {
        stage: "planner_revise",
        label: "Improving the draft plan",
        defaultDetail: "Applying self-review to produce a better itinerary.",
    },
    {
        stage: "insert_nodes",
        label: "Finalizing itinerary",
        defaultDetail: "Preparing the final plan payload and syncing results.",
    },
];

const countryDataLoader = import.meta.glob<{ default: ICountry[] }>(
    "../../node_modules/@countrystatecity/countries/dist/data/countries.json",
);
const stateDataLoaders = import.meta.glob<{ default: IState[] }>(
    "../../node_modules/@countrystatecity/countries/dist/data/*/states.json",
);

const statePathByCountryIso = new Map<string, string>();

for (const path of Object.keys(stateDataLoaders)) {
    const match = path.match(/\/data\/([^/]+)\/states\.json$/);
    if (!match) continue;
    const countryDir = match[1];
    const countryIso = countryDir.split("-").pop();
    if (!countryIso) continue;
    statePathByCountryIso.set(countryIso, path);
}

async function loadCountriesFromPackageData(): Promise<ICountry[]> {
    const loader = Object.values(countryDataLoader)[0];
    if (!loader) return [];
    const module = await loader();
    return module.default ?? [];
}

async function loadStatesFromPackageData(countryCode: string): Promise<IState[]> {
    const path = statePathByCountryIso.get(countryCode);
    if (!path) return [];
    const loader = stateDataLoaders[path];
    if (!loader) return [];
    const module = await loader();
    return module.default ?? [];
}

export default function PlanPage() {
    const navigate = useNavigate();
    const { session } = useAuth();

    const [nodes, setNodes] = useState<ItineraryNode[]>([]);
    const [tripId, setTripId] = useState<string | null>(null);
    const [pendingFiles, setPendingFiles] = useState<File[]>([]);
    const [pendingLinks, setPendingLinks] = useState<string[]>([]);
    const [linkInput, setLinkInput] = useState("");
    const [linkError, setLinkError] = useState<string | null>(null);
    const [countries, setCountries] = useState<ICountry[]>([]);
    const [states, setStates] = useState<IState[]>([]);
    const [countryCodeInput, setCountryCodeInput] = useState("");
    const [stateCodeInput, setStateCodeInput] = useState("");
    const [countryNameInput, setCountryNameInput] = useState("");
    const [stateNameInput, setStateNameInput] = useState("");
    const [countriesLoading, setCountriesLoading] = useState(false);
    const [statesLoading, setStatesLoading] = useState(false);
    const [tripWindowMode, setTripWindowMode] =
        useState<"fixed" | "not_decided">("fixed");
    const [startDateInput, setStartDateInput] = useState("");
    const [endDateInput, setEndDateInput] = useState("");
    const [tripDaysInput, setTripDaysInput] = useState("5");
    const [hardConstraintsInput, setHardConstraintsInput] = useState("");
    const [softPreferencesInput, setSoftPreferencesInput] = useState("");
    const [mustIncludeInput, setMustIncludeInput] = useState("");
    const [avoidInput, setAvoidInput] = useState("");
    const [debugMode, setDebugMode] = useState(true);
    const [debugPayload, setDebugPayload] =
        useState<ProcessIdeaDebugResponse | null>(null);
    const [plannerReasoning, setPlannerReasoning] = useState<string | null>(null);
    const [plannerCritique, setPlannerCritique] = useState<string | null>(null);
    const [lifestyleProfile, setLifestyleProfile] = useState<LifestyleProfile | null>(
        () => loadLifestyleProfile(),
    );
    const [showLifestyleModal, setShowLifestyleModal] = useState(false);
    const pendingIdeaRef = useRef<string | null>(null);
    const [urlScraperLoading, setUrlScraperLoading] = useState(false);
    const [urlScraperError, setUrlScraperError] = useState<string | null>(null);
    const [urlScraperResult, setUrlScraperResult] =
        useState<UrlScraperDebugResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const nodeQueueRef = useRef<ItineraryNode[]>([]);
    const drainTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const extractingRef = useRef(false);
    const [loadingPhaseIndex, setLoadingPhaseIndex] = useState(0);
    const [loadingPhaseLabel, setLoadingPhaseLabel] = useState(
        LOADING_PHASES[0].label,
    );
    const [loadingPhaseDetail, setLoadingPhaseDetail] = useState(
        LOADING_PHASES[0].defaultDetail,
    );
    const [error, setError] = useState<string | null>(null);

    // ── Scaffold review state (human-in-the-loop) ───────────────────────────
    const [scaffoldReady, setScaffoldReady] = useState(false);
    const [scaffoldText, setScaffoldText] = useState<string | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [revisionCount, setRevisionCount] = useState(0);
    const [scaffoldFeedback, setScaffoldFeedback] = useState("");
    const [revisingScaffold, setRevisingScaffold] = useState(false);

    // ── Agent action feed state ─────────────────────────────────────────────
    const [agentActions, setAgentActions] = useState<AgentActionEvent[]>([]);
    const [agentScratchpad, setAgentScratchpad] = useState("");
    const [agentActive, setAgentActive] = useState(false);
    const planRequestIdRef = useRef<string | null>(null);
    const wasCancelledRef = useRef(false);

    // Question handling
    const [pendingQuestion, setPendingQuestion] = useState<AgentQuestionEvent | null>(null);
    const [submittingAnswer, setSubmittingAnswer] = useState(false);

    // Track extraction completion
    const [done, setDone] = useState(false);

    const handleAnswer = async (answer: string) => {
        if (!pendingQuestion || !planRequestIdRef.current) return;
        setSubmittingAnswer(true);
        try {
            const token = session?.access_token;
            await fetch(`${(import.meta.env.VITE_API_BASE as string) ?? "http://localhost:8000/api"}/ideas/answer-question`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                },
                body: JSON.stringify({
                    request_id: planRequestIdRef.current,
                    question_id: pendingQuestion.question_id,
                    answer,
                }),
            });
            setPendingQuestion(null);
        } finally {
            setSubmittingAnswer(false);
        }
    };

    const parseLines = (value: string) =>
        value
            .split("\n")
            .map((line) => line.trim())
            .filter((line) => line.length > 0);

    const normalizeLookupValue = (value: string) => value.trim().toLowerCase();

    const selectedCountry =
        countries.find((country) => country.iso2 === countryCodeInput) ?? null;
    const selectedState =
        states.find((state) => state.iso2 === stateCodeInput) ?? null;
    const resolvedTripLocation =
        selectedCountry && selectedState
            ? `${selectedState.name}, ${selectedCountry.name}`
            : selectedCountry?.name || undefined;
    const stageIndexMap = new Map(
        LOADING_PHASES.map((phase, index) => [phase.stage, index]),
    );

    useEffect(() => {
        let cancelled = false;

        const loadCountries = async () => {
            setCountriesLoading(true);
            try {
                let fetchedCountries: ICountry[] = [];
                try {
                    fetchedCountries = await getCountries();
                } catch (fetchError) {
                    console.warn(
                        "Package country loader failed, falling back to direct data import.",
                        fetchError,
                    );
                }
                if (fetchedCountries.length === 0) {
                    fetchedCountries = await loadCountriesFromPackageData();
                }
                if (!cancelled) {
                    setCountries([...fetchedCountries].sort(sortByName));
                }
            } catch (fetchError) {
                console.error("Failed to fetch countries", fetchError);
            } finally {
                if (!cancelled) {
                    setCountriesLoading(false);
                }
            }
        };

        void loadCountries();
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        let cancelled = false;

        if (!countryCodeInput) {
            setStates([]);
            setStateCodeInput("");
            setStateNameInput("");
            return () => {
                cancelled = true;
            };
        }

        const loadStates = async () => {
            setStatesLoading(true);
            try {
                let fetchedStates: IState[] = [];
                try {
                    fetchedStates = await getStatesOfCountry(countryCodeInput);
                } catch (fetchError) {
                    console.warn(
                        "Package state loader failed, falling back to direct data import.",
                        fetchError,
                    );
                }
                if (fetchedStates.length === 0) {
                    fetchedStates =
                        await loadStatesFromPackageData(countryCodeInput);
                }
                if (!cancelled) {
                    setStates([...fetchedStates].sort(sortByName));
                }
            } catch (fetchError) {
                console.error("Failed to fetch states", fetchError);
                if (!cancelled) {
                    setStates([]);
                }
            } finally {
                if (!cancelled) {
                    setStatesLoading(false);
                }
            }
        };

        setStateCodeInput("");
        setStateNameInput("");
        void loadStates();

        return () => {
            cancelled = true;
        };
    }, [countryCodeInput]);

    const handleCountryNameChange = (value: string) => {
        setCountryNameInput(value);
        const normalized = normalizeLookupValue(value);
        const matchedCountry = countries.find(
            (country) => normalizeLookupValue(country.name) === normalized,
        );

        if (!matchedCountry) {
            setCountryCodeInput("");
            return;
        }

        setCountryCodeInput(matchedCountry.iso2);
        setCountryNameInput(matchedCountry.name);
    };

    const handleStateNameChange = (value: string) => {
        setStateNameInput(value);
        const normalized = normalizeLookupValue(value);
        const matchedState = states.find(
            (state) => normalizeLookupValue(state.name) === normalized,
        );

        if (!matchedState) {
            setStateCodeInput("");
            return;
        }

        setStateCodeInput(matchedState.iso2);
        setStateNameInput(matchedState.name);
    };

    const handleSubmit = async (idea: string, profileOverride?: LifestyleProfile | null) => {
        // If no saved lifestyle profile, show modal first (unless explicitly skipped)
        const resolvedProfile = profileOverride !== undefined ? profileOverride : lifestyleProfile;
        if (resolvedProfile === null && profileOverride === undefined) {
            pendingIdeaRef.current = idea;
            setShowLifestyleModal(true);
            return;
        }

        setLoading(true);
        setError(null);
        setNodes([]);
        nodeQueueRef.current = [];
        if (drainTimerRef.current !== null) {
            clearInterval(drainTimerRef.current);
            drainTimerRef.current = null;
        }
        setDebugPayload(null);
        setPlannerReasoning(null);
        setPlannerCritique(null);
        setLoadingPhaseIndex(0);
        setLoadingPhaseLabel(LOADING_PHASES[0].label);
        setLoadingPhaseDetail(LOADING_PHASES[0].defaultDetail);

        let inputDirectives: InputDirectives = {
            hard_constraints: parseLines(hardConstraintsInput),
            soft_preferences: parseLines(softPreferencesInput),
            must_include: parseLines(mustIncludeInput),
            avoid: parseLines(avoidInput),
        };

        // Merge lifestyle profile into directives
        if (resolvedProfile) {
            inputDirectives = lifestyleToDirectives(resolvedProfile, inputDirectives);
        }

        let resolvedTripDays: number | undefined;
        if (tripWindowMode === "fixed") {
            if (!startDateInput || !endDateInput) {
                setError(
                    "Please select both start and end dates, or switch to 'Not decided yet' and enter trip days.",
                );
                setLoading(false);
                return;
            }
            if (startDateInput > endDateInput) {
                setError("Start date must be earlier than or equal to end date.");
                setLoading(false);
                return;
            }
        } else {
            const parsedDays = Number.parseInt(tripDaysInput, 10);
            if (!Number.isFinite(parsedDays) || parsedDays < 1 || parsedDays > 60) {
                setError("Trip days must be a whole number between 1 and 60.");
                setLoading(false);
                return;
            }
            resolvedTripDays = parsedDays;
        }

        try {
            const res = await processIdeaStream(
                idea,
                {
                    tripId: tripId ?? undefined,
                    tripLocation: resolvedTripLocation,
                    startDate:
                        tripWindowMode === "fixed" ? startDateInput || undefined : undefined,
                    endDate:
                        tripWindowMode === "fixed" ? endDateInput || undefined : undefined,
                    tripWindowMode,
                    tripDays: tripWindowMode === "not_decided" ? resolvedTripDays : undefined,
                    files: pendingFiles,
                    links: pendingLinks,
                    inputDirectives,
                    debug: debugMode,
                },
                {
                    onEvent: ({ event, data }) => {
                        if (event === "accepted") {
                            if (typeof data.request_id === "string") {
                                planRequestIdRef.current = data.request_id;
                            }
                            setLoadingPhaseLabel("Pipeline accepted");
                            setLoadingPhaseDetail(
                                "Starting staged planning workflow.",
                            );
                            return;
                        }

                        if (event === "stage_start") {
                            const stage = String(data.stage ?? "");
                            const idx = stageIndexMap.get(stage);
                            if (typeof idx === "number") {
                                setLoadingPhaseIndex(idx);
                            }
                            setLoadingPhaseLabel(
                                typeof data.label === "string"
                                    ? data.label
                                    : "Processing stage",
                            );
                            if (typeof data.detail === "string") {
                                setLoadingPhaseDetail(data.detail);
                            }
                            return;
                        }

                        if (event === "stage_done") {
                            const stage = String(data.stage ?? "");
                            const idx = stageIndexMap.get(stage);
                            if (typeof idx === "number") {
                                setLoadingPhaseIndex(idx);
                            }
                            const elapsedMs = Number(data.elapsed_ms ?? 0);
                            if (Number.isFinite(elapsedMs) && elapsedMs > 0) {
                                setLoadingPhaseDetail(
                                    `Completed in ${(elapsedMs / 1000).toFixed(1)}s`,
                                );
                            } else {
                                setLoadingPhaseDetail("Completed.");
                            }
                            return;
                        }

                        if (event === "node_batch") {
                            const incoming = asItineraryNodeArray(data.nodes);
                            if (incoming.length > 0) {
                                nodeQueueRef.current.push(...incoming);
                            }

                            const sequence = Number(data.sequence ?? NaN);
                            const total = Number(data.total_batches ?? NaN);
                            const day =
                                typeof data.day === "string"
                                    ? data.day
                                    : "day segment";
                            if (Number.isFinite(sequence) && Number.isFinite(total)) {
                                setLoadingPhaseLabel(
                                    `Building your itinerary (day ${sequence} of ${total})`,
                                );
                                setLoadingPhaseDetail(
                                    `Queued ${incoming.length} activities for ${day}.`,
                                );
                            } else {
                                setLoadingPhaseLabel("Building your itinerary");
                                setLoadingPhaseDetail(
                                    `Queued ${incoming.length} activities.`,
                                );
                            }

                            if (drainTimerRef.current === null) {
                                drainTimerRef.current = setInterval(() => {
                                    const next = nodeQueueRef.current.shift();
                                    if (next) {
                                        setNodes((prev) =>
                                            mergeNodesByIdentity(prev, [next]),
                                        );
                                    } else {
                                        clearInterval(drainTimerRef.current!);
                                        drainTimerRef.current = null;
                                    }
                                }, 80);
                            }
                            return;
                        }

                        if (event === "error") {
                            const message =
                                typeof data.message === "string"
                                    ? data.message
                                    : "Streaming failed.";
                            setLoadingPhaseLabel("Pipeline error");
                            setLoadingPhaseDetail(message);
                            return;
                        }

                        if (event === "planner_reasoning") {
                            const reasoningText =
                                typeof data.text === "string"
                                    ? data.text.trim()
                                    : "";
                            if (reasoningText.length > 0) {
                                setPlannerReasoning(reasoningText);
                                setLoadingPhaseLabel("Planner draft ready");
                                setLoadingPhaseDetail(
                                    "Generated natural-language reasoning before final node extraction.",
                                );
                            }
                        }

                        if (event === "planner_critique") {
                            const critiqueText =
                                typeof data.text === "string" ? data.text.trim() : "";
                            if (critiqueText.length > 0) {
                                setPlannerCritique(critiqueText);
                                const idx = stageIndexMap.get("planner_critique");
                                if (typeof idx === "number") setLoadingPhaseIndex(idx);
                                setLoadingPhaseLabel("Reviewing the draft plan");
                                setLoadingPhaseDetail("Self-review complete.");
                            }
                        }

                        if (event === "planner_revised_reasoning") {
                            const revisedText =
                                typeof data.text === "string" ? data.text.trim() : "";
                            if (revisedText.length > 0) {
                                setPlannerReasoning(revisedText);
                                const idx = stageIndexMap.get("planner_revise");
                                if (typeof idx === "number") setLoadingPhaseIndex(idx);
                                setLoadingPhaseLabel("Improved draft ready");
                                setLoadingPhaseDetail("Applied self-review to itinerary.");
                            }
                        }
                    },
                },
            );
            setNodes(res.nodes);
            setTripId(res.trip_id);
            setPlannerReasoning((prev) => {
                const candidate = res.planner_scaffold_text;
                if (typeof candidate === "string" && candidate.trim().length > 0) {
                    return candidate.trim();
                }
                return prev;
            });
            if (debugMode) {
                setDebugPayload(res as ProcessIdeaDebugResponse);
            } else {
                setDebugPayload(null);
            }
            setPendingFiles([]);
            setPendingLinks([]);
            setLinkInput("");
            setLinkError(null);
        } catch (err: unknown) {
            setError(
                err instanceof Error ? err.message : "Something went wrong",
            );
        } finally {
            if (drainTimerRef.current !== null) {
                clearInterval(drainTimerRef.current);
                drainTimerRef.current = null;
            }
            if (nodeQueueRef.current.length > 0) {
                const remaining = [...nodeQueueRef.current];
                nodeQueueRef.current = [];
                setNodes((prev) => mergeNodesByIdentity(prev, remaining));
            }
            setLoading(false);
        }
    };

    // ── Human-in-the-loop plan handlers ────────────────────────────────────

    /**
     * Step 1: send idea to /api/ideas/plan (SSE), await scaffold_ready, show review card.
     */
    const handlePlanRequest = async (idea: string, profileOverride?: LifestyleProfile | null) => {
        const resolvedProfile = profileOverride !== undefined ? profileOverride : lifestyleProfile;
        if (resolvedProfile === null && profileOverride === undefined) {
            pendingIdeaRef.current = idea;
            setShowLifestyleModal(true);
            return;
        }

        setLoading(true);
        setError(null);
        setNodes([]);
        setScaffoldReady(false);
        setScaffoldText(null);
        setSessionId(null);
        setRevisionCount(0);
        setScaffoldFeedback("");
        wasCancelledRef.current = false;
        planRequestIdRef.current = null;
        nodeQueueRef.current = [];
        if (drainTimerRef.current !== null) {
            clearInterval(drainTimerRef.current);
            drainTimerRef.current = null;
        }
        setDebugPayload(null);
        setPlannerReasoning(null);
        setPlannerCritique(null);
        setLoadingPhaseIndex(0);
        setLoadingPhaseLabel(LOADING_PHASES[0].label);
        setLoadingPhaseDetail(LOADING_PHASES[0].defaultDetail);

        let inputDirectives: InputDirectives = {
            hard_constraints: parseLines(hardConstraintsInput),
            soft_preferences: parseLines(softPreferencesInput),
            must_include: parseLines(mustIncludeInput),
            avoid: parseLines(avoidInput),
        };
        if (resolvedProfile) {
            inputDirectives = lifestyleToDirectives(resolvedProfile, inputDirectives);
        }

        let resolvedTripDays: number | undefined;
        if (tripWindowMode === "fixed") {
            if (!startDateInput || !endDateInput) {
                setError("Please select both start and end dates, or switch to 'Not decided yet' and enter trip days.");
                setLoading(false);
                return;
            }
            if (startDateInput > endDateInput) {
                setError("Start date must be earlier than or equal to end date.");
                setLoading(false);
                return;
            }
        } else {
            const parsedDays = Number.parseInt(tripDaysInput, 10);
            if (!Number.isFinite(parsedDays) || parsedDays < 1 || parsedDays > 60) {
                setError("Trip days must be a whole number between 1 and 60.");
                setLoading(false);
                return;
            }
            resolvedTripDays = parsedDays;
        }

        try {
            const scaffoldEvent = await planIdeaStream(
                idea,
                {
                    tripId: tripId ?? undefined,
                    tripLocation: resolvedTripLocation,
                    startDate: tripWindowMode === "fixed" ? startDateInput || undefined : undefined,
                    endDate: tripWindowMode === "fixed" ? endDateInput || undefined : undefined,
                    tripWindowMode,
                    tripDays: tripWindowMode === "not_decided" ? resolvedTripDays : undefined,
                    files: pendingFiles,
                    links: pendingLinks,
                    inputDirectives,
                },
                {
                    onEvent: ({ event, data }) => {
                        if (event === "stage_start") {
                            const stage = String(data.stage ?? "");
                            const idx = stageIndexMap.get(stage);
                            if (typeof idx === "number") setLoadingPhaseIndex(idx);
                            setLoadingPhaseLabel(typeof data.label === "string" ? data.label : "Processing stage");
                            if (typeof data.detail === "string") setLoadingPhaseDetail(data.detail);
                            if (stage === "build_scaffold") {
                                setAgentActive(true);
                                setAgentActions([]);
                                setAgentScratchpad("");
                            }
                            return;
                        }
                        if (event === "stage_done") {
                            const elapsedMs = Number(data.elapsed_ms ?? 0);
                            if (Number.isFinite(elapsedMs) && elapsedMs > 0) {
                                setLoadingPhaseDetail(`Completed in ${(elapsedMs / 1000).toFixed(1)}s`);
                            } else {
                                setLoadingPhaseDetail("Completed.");
                            }
                            return;
                        }
                        if (event === "agent_action") {
                            const actionData = data as AgentActionEvent;
                            setAgentActions(prev => [...prev, actionData]);
                            if (actionData.scratchpad) {
                                setAgentScratchpad(actionData.scratchpad);
                            }
                            return;
                        }
                        if (event === "agent_question") {
                            setPendingQuestion(data as AgentQuestionEvent);
                            return;
                        }
                        if (event === "agent_cancelled") {
                            wasCancelledRef.current = true;
                            setAgentActive(false);
                            setLoadingPhaseLabel("Planning stopped");
                            setLoadingPhaseDetail("You stopped the agent. Submit again to restart.");
                            return;
                        }
                        if (event === "error") {
                            const message = typeof data.message === "string" ? data.message : "Planning failed.";
                            setError(message);
                        }
                    },
                },
            );
            // Set scaffold state from the authoritative resolved event
            setScaffoldText(scaffoldEvent.scaffold_text);
            setSessionId(scaffoldEvent.session_id);
            setRevisionCount(scaffoldEvent.revision_count);
            setScaffoldReady(true);
            setAgentActive(false);
            setAgentScratchpad(scaffoldEvent.scratchpad || "");
            setLoadingPhaseLabel("Draft plan ready");
            setLoadingPhaseDetail("Review and approve your plan to generate the full itinerary.");
        } catch (err: unknown) {
            if (!wasCancelledRef.current) {
                setError(err instanceof Error ? err.message : "Something went wrong");
            }
        } finally {
            setLoading(false);
        }
    };

    /**
     * Step 2 (optional): revise scaffold based on user feedback.
     */
    const handleRevise = async () => {
        if (!sessionId || !scaffoldText || revisionCount >= 1) return;

        setRevisingScaffold(true);
        try {
            const result = await reviseScaffold(sessionId, scaffoldText, scaffoldFeedback);
            setScaffoldText(result.scaffold_text);
            setRevisionCount(result.revision_count);
            setScaffoldFeedback("");
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Revision failed");
        } finally {
            setRevisingScaffold(false);
        }
    };

    /**
     * Step 3: extract full itinerary from approved scaffold.
     */
    const handleExtract = async (approvedScaffold: string) => {
        if (!sessionId) {
            console.warn("[handleExtract] no sessionId, aborting");
            return;
        }
        if (extractingRef.current) {
            console.warn("[handleExtract] already extracting, skipping duplicate call");
            return;
        }
        extractingRef.current = true;
        console.log("[handleExtract] START sessionId=", sessionId);

        setLoading(true);
        setScaffoldReady(false);
        setError(null);
        setNodes([]);
        nodeQueueRef.current = [];
        if (drainTimerRef.current !== null) {
            clearInterval(drainTimerRef.current);
            drainTimerRef.current = null;
        }
        setDebugPayload(null);
        setLoadingPhaseIndex(0);
        setLoadingPhaseLabel("Generating itinerary");
        setLoadingPhaseDetail("Extracting structured plan from approved scaffold.");

        try {
            const res = await extractIdeaStream(
                sessionId,
                approvedScaffold,
                {
                    onEvent: ({ event, data }) => {
                        if (event === "final" || event === "error") {
                            console.log("[handleExtract] SSE event:", event, JSON.stringify({ trip_id: data.trip_id, nodeCount: Array.isArray(data.nodes) ? (data.nodes as unknown[]).length : "N/A" }));
                        }
                        if (event === "stage_start") {
                            const stage = String(data.stage ?? "");
                            const idx = stageIndexMap.get(stage);
                            if (typeof idx === "number") setLoadingPhaseIndex(idx);
                            setLoadingPhaseLabel(typeof data.label === "string" ? data.label : "Processing stage");
                            if (typeof data.detail === "string") setLoadingPhaseDetail(data.detail);
                            return;
                        }
                        if (event === "stage_done") {
                            const elapsedMs = Number(data.elapsed_ms ?? 0);
                            if (Number.isFinite(elapsedMs) && elapsedMs > 0) {
                                setLoadingPhaseDetail(`Completed in ${(elapsedMs / 1000).toFixed(1)}s`);
                            } else {
                                setLoadingPhaseDetail("Completed.");
                            }
                            return;
                        }
                        if (event === "node_batch") {
                            const incoming = asItineraryNodeArray(data.nodes);
                            if (incoming.length > 0) nodeQueueRef.current.push(...incoming);
                            const sequence = Number(data.sequence ?? NaN);
                            const total = Number(data.total_batches ?? NaN);
                            const day = typeof data.day === "string" ? data.day : "day segment";
                            if (Number.isFinite(sequence) && Number.isFinite(total)) {
                                setLoadingPhaseLabel(`Building your itinerary (day ${sequence} of ${total})`);
                                setLoadingPhaseDetail(`Queued ${incoming.length} activities for ${day}.`);
                            } else {
                                setLoadingPhaseLabel("Building your itinerary");
                                setLoadingPhaseDetail(`Queued ${incoming.length} activities.`);
                            }
                            if (drainTimerRef.current === null) {
                                drainTimerRef.current = setInterval(() => {
                                    const next = nodeQueueRef.current.shift();
                                    if (next) {
                                        setNodes((prev) => mergeNodesByIdentity(prev, [next]));
                                    } else {
                                        clearInterval(drainTimerRef.current!);
                                        drainTimerRef.current = null;
                                    }
                                }, 80);
                            }
                            return;
                        }
                        if (event === "error") {
                            const message = typeof data.message === "string" ? data.message : "Extraction failed.";
                            setError(message);
                        }
                    },
                },
                debugMode,
            );
            console.log("[handleExtract] extractIdeaStream resolved", {
                trip_id: res.trip_id,
                nodeCount: res.nodes?.length ?? 0,
            });
            setNodes(res.nodes);
            setTripId(res.trip_id);
            setDone(true);
            // Navigate to TripPage after extraction
            if (res.trip_id) {
                if (!res.nodes || res.nodes.length === 0) {
                    console.warn("[handleExtract] 0 nodes returned, NOT navigating");
                    setError("Plan generation didn't produce any activities. Please approve again or revise the draft.");
                    return;
                }
                console.log("[handleExtract] navigating to /trips/" + res.trip_id, "with", res.nodes.length, "nodes");
                sessionStorage.setItem(
                    `nova_session_${res.trip_id}`,
                    JSON.stringify({ agentActions, agentScratchpad }),
                );
                navigate(`/trips/${res.trip_id}`, {
                    state: { initialNodes: res.nodes, agentScratchpad, agentActions, tripLocation: resolvedTripLocation ?? null },
                });
                return;
            }
            console.warn("[handleExtract] no trip_id in response, staying on PlanPage");
            if (debugMode) {
                setDebugPayload(res as ProcessIdeaDebugResponse);
            }
            setPendingFiles([]);
            setPendingLinks([]);
            setLinkInput("");
            setLinkError(null);
            setSessionId(null);
        } catch (err: unknown) {
            console.error("[handleExtract] CAUGHT ERROR", err);
            setError(err instanceof Error ? err.message : "Something went wrong");
        } finally {
            if (drainTimerRef.current !== null) {
                clearInterval(drainTimerRef.current);
                drainTimerRef.current = null;
            }
            if (nodeQueueRef.current.length > 0) {
                const remaining = [...nodeQueueRef.current];
                nodeQueueRef.current = [];
                setNodes((prev) => mergeNodesByIdentity(prev, remaining));
            }
            setLoading(false);
            extractingRef.current = false;
        }
    };

    const handleFilesAdded = (files: File[]) => {
        setPendingFiles((prev) => [...prev, ...files]);
    };

    const isValidUrl = (value: string) => {
        try {
            const url = new URL(value);
            return url.protocol === "http:" || url.protocol === "https:";
        } catch {
            return false;
        }
    };

    const handleAddLink = (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const trimmed = linkInput.trim();
        if (trimmed.length === 0) return;

        if (!isValidUrl(trimmed)) {
            setLinkError("Please enter a valid URL.");
            return;
        }

        setPendingLinks((prev) => [...prev, trimmed]);
        setLinkInput("");
        setLinkError(null);
    };

    const removePendingFile = (index: number) => {
        setPendingFiles((prev) => prev.filter((_, i) => i !== index));
    };

    const removePendingLink = (index: number) => {
        setPendingLinks((prev) => prev.filter((_, i) => i !== index));
    };

    const handleDebugUrlScraper = async () => {
        if (pendingLinks.length === 0) {
            setUrlScraperError("Add at least one link in the staging area first.");
            setUrlScraperResult(null);
            return;
        }

        setUrlScraperLoading(true);
        setUrlScraperError(null);
        try {
            const result = await debugUrlScraper(pendingLinks);
            setUrlScraperResult(result);
        } catch (err: unknown) {
            setUrlScraperError(
                err instanceof Error
                    ? err.message
                    : "URL scraper debug request failed.",
            );
            setUrlScraperResult(null);
        } finally {
            setUrlScraperLoading(false);
        }
    };

    const handleLifestyleConfirm = (profile: LifestyleProfile) => {
        saveLifestyleProfile(profile);
        setLifestyleProfile(profile);
        setShowLifestyleModal(false);
        const idea = pendingIdeaRef.current;
        pendingIdeaRef.current = null;
        if (idea !== null) {
            void handlePlanRequest(idea, profile);
        }
    };

    const handleLifestyleSkip = () => {
        setShowLifestyleModal(false);
        const idea = pendingIdeaRef.current;
        pendingIdeaRef.current = null;
        // Pass empty object sentinel (not null) so we skip the modal next time
        const skippedProfile: LifestyleProfile = {
            wake_time_pref: null,
            travel_party: [],
            dietary: [],
            fitness_level: null,
            accommodation_style: null,
            pace: null,
            mobility_mode: null,
        };
        saveLifestyleProfile(skippedProfile);
        setLifestyleProfile(skippedProfile);
        if (idea !== null) {
            void handlePlanRequest(idea, skippedProfile);
        }
    };

    const isPlanning = loading || scaffoldReady || agentActive;

    return (
        <>
            {showLifestyleModal && (
                <LifestyleModal
                    onConfirm={handleLifestyleConfirm}
                    onSkip={handleLifestyleSkip}
                />
            )}
            <div className="sky-bg" />
            <div className="sky-arc sky-arc-1" />
            <div className="sky-arc sky-arc-2" />
            <div className="sky-cloud sky-cloud-1" />
            <div className="sky-cloud sky-cloud-2" />
            <div className="sky-cloud sky-cloud-3" />
            <div className="sky-cloud sky-cloud-4" />
            <div className="sky-cloud sky-cloud-5" />
            <button
                className="sky-wordmark"
                onClick={() => navigate("/dashboard")}
                style={{ background: "none", border: "none", cursor: "pointer" }}
            >
                <span className="sky-wordmark-star">✦</span>
                NovaSync
            </button>

            <div className="group-plan-page" style={{ position: "relative", zIndex: 10, maxWidth: 820, margin: "0 auto", padding: "80px 1.5rem 3rem" }}>
                {/* Back to dashboard */}
                <button
                    onClick={() => navigate("/dashboard")}
                    style={{ background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 6, color: "#4a6a8a", fontSize: "0.88rem", padding: "4px 0", marginBottom: 24, fontFamily: "'DM Sans', sans-serif" }}
                >
                    <span>←</span> Back to dashboard
                </button>

                <h1 style={{ fontFamily: "'Cormorant Garamond', serif", color: "#0b1f38", fontSize: "2rem", margin: "0 0 1.5rem" }}>
                    {isPlanning ? "Planning your trip..." : "Plan a new trip"}
                </h1>

                {error && (
                    <div style={{ background: "rgba(192,57,43,0.1)", border: "1px solid rgba(192,57,43,0.3)", borderRadius: 10, padding: "1rem 1.2rem", marginBottom: 20, color: "#c0392b", fontSize: "0.88rem" }}>
                        {error}
                    </div>
                )}

                {/* Phase 1: Form (visible when not planning) */}
                {!isPlanning && (
                    <div className="glass-card" style={{ padding: "1.6rem 1.8rem", marginBottom: 20 }}>
                        <div style={{ marginBottom: 16 }}>
                            <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "1.2rem", color: "#0b1f38", margin: "0 0 8px" }}>Idea Drop Zone</h2>
                            <p style={{ fontSize: "0.83rem", color: "#4a6a8a", margin: "0 0 12px" }}>Drop screenshots or short videos, then describe your trip below.</p>
                            <IdeaDropzone onFilesAdded={handleFilesAdded} />
                            <form className="link-input-row" onSubmit={handleAddLink} style={{ display: "flex", gap: 8, marginTop: 10 }}>
                                <input
                                    type="url"
                                    className="sky-input"
                                    value={linkInput}
                                    onChange={(event) => { setLinkInput(event.target.value); if (linkError) setLinkError(null); }}
                                    placeholder="Paste an Instagram, TikTok, or web link..."
                                    disabled={loading}
                                    style={{ flex: 1 }}
                                />
                                <button type="submit" className="sky-btn-primary" disabled={loading || linkInput.trim().length === 0} style={{ padding: "8px 18px" }}>Add</button>
                            </form>
                            {linkError && <p style={{ color: "#c0392b", fontSize: "0.82rem", marginTop: 4 }}>{linkError}</p>}
                            {(pendingFiles.length > 0 || pendingLinks.length > 0) && (
                                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
                                    {pendingFiles.map((file, index) => (
                                        <span key={`${file.name}-${index}`} style={{ background: "rgba(74,141,196,0.1)", border: "1px solid rgba(74,141,196,0.25)", borderRadius: 999, padding: "3px 12px", fontSize: "0.82rem", color: "#1e3a5f", display: "flex", alignItems: "center", gap: 6 }}>
                                            {file.name}
                                            <button type="button" onClick={() => removePendingFile(index)} style={{ background: "none", border: "none", cursor: "pointer", color: "#c0392b", fontSize: "0.9rem", padding: 0 }}>x</button>
                                        </span>
                                    ))}
                                    {pendingLinks.map((link, index) => (
                                        <span key={`${link}-${index}`} style={{ background: "rgba(74,141,196,0.1)", border: "1px solid rgba(74,141,196,0.25)", borderRadius: 999, padding: "3px 12px", fontSize: "0.82rem", color: "#1e3a5f", display: "flex", alignItems: "center", gap: 6 }}>
                                            {link.length > 40 ? link.slice(0, 40) + "..." : link}
                                            <button type="button" onClick={() => removePendingLink(index)} style={{ background: "none", border: "none", cursor: "pointer", color: "#c0392b", fontSize: "0.9rem", padding: 0 }}>x</button>
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>

                        <div style={{ marginBottom: 16 }}>
                            <h3 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "1rem", color: "#0b1f38", margin: "0 0 8px" }}>Trip Context</h3>
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                                <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                    <span>Country</span>
                                    <input type="text" list="country-options" className="sky-input" value={countryNameInput} onChange={(e) => handleCountryNameChange(e.target.value)} disabled={loading || countriesLoading} placeholder={countriesLoading ? "Loading..." : "Type to search"} />
                                    <datalist id="country-options">{countries.map((c) => <option key={c.iso2} value={c.name} />)}</datalist>
                                </label>
                                <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                    <span>State / Region</span>
                                    <input type="text" list="state-options" className="sky-input" value={stateNameInput} onChange={(e) => handleStateNameChange(e.target.value)} disabled={loading || !countryCodeInput || statesLoading} placeholder={!countryCodeInput ? "Choose country first" : statesLoading ? "Loading..." : "Type to search"} />
                                    <datalist id="state-options">{states.map((s) => <option key={s.iso2} value={s.name} />)}</datalist>
                                </label>
                                <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                    <span>Date planning</span>
                                    <select className="sky-input" value={tripWindowMode} onChange={(e) => setTripWindowMode(e.target.value as "fixed" | "not_decided")} disabled={loading}>
                                        <option value="fixed">I know exact dates</option>
                                        <option value="not_decided">Not decided yet</option>
                                    </select>
                                </label>
                                {tripWindowMode === "fixed" ? (
                                    <>
                                        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                            <span>Start date</span>
                                            <input type="date" className="sky-input" value={startDateInput} onChange={(e) => setStartDateInput(e.target.value)} disabled={loading} required />
                                        </label>
                                        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                            <span>End date</span>
                                            <input type="date" className="sky-input" value={endDateInput} onChange={(e) => setEndDateInput(e.target.value)} disabled={loading} required />
                                        </label>
                                    </>
                                ) : (
                                    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                        <span>Trip length (days)</span>
                                        <input type="number" className="sky-input" min={1} max={60} step={1} value={tripDaysInput} onChange={(e) => setTripDaysInput(e.target.value)} placeholder="5" disabled={loading} required />
                                    </label>
                                )}
                            </div>
                        </div>

                        <div style={{ marginBottom: 16 }}>
                            <h3 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "1rem", color: "#0b1f38", margin: "0 0 8px" }}>Planning Directives</h3>
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                                <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                    <span>Hard constraints</span>
                                    <textarea className="sky-input" value={hardConstraintsInput} onChange={(e) => setHardConstraintsInput(e.target.value)} rows={2} placeholder="Only drive before sunset" disabled={loading} />
                                </label>
                                <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                    <span>Soft preferences</span>
                                    <textarea className="sky-input" value={softPreferencesInput} onChange={(e) => setSoftPreferencesInput(e.target.value)} rows={2} placeholder="Prefer scenic routes" disabled={loading} />
                                </label>
                                <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                    <span>Must include</span>
                                    <textarea className="sky-input" value={mustIncludeInput} onChange={(e) => setMustIncludeInput(e.target.value)} rows={2} placeholder="MONA" disabled={loading} />
                                </label>
                                <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.83rem", color: "#4a6a8a" }}>
                                    <span>Avoid</span>
                                    <textarea className="sky-input" value={avoidInput} onChange={(e) => setAvoidInput(e.target.value)} rows={2} placeholder="Late-night driving" disabled={loading} />
                                </label>
                            </div>
                            <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: "0.82rem", color: "#4a6a8a" }}>
                                <input type="checkbox" checked={debugMode} onChange={(e) => setDebugMode(e.target.checked)} disabled={loading} />
                                Include backend debug payload
                            </label>
                        </div>

                        <IdeaInput
                            onSubmit={handlePlanRequest}
                            loading={loading}
                            loadingPhaseLabel={loadingPhaseLabel}
                            loadingPhaseDetail={loadingPhaseDetail}
                            loadingStepIndex={loadingPhaseIndex}
                            loadingTotalSteps={LOADING_PHASES.length}
                        />
                    </div>
                )}

                {/* Phase 2: Planning active — agent feed + scaffold review + question card */}
                {isPlanning && (
                    <>
                        {/* Loading indicator */}
                        {loading && !scaffoldReady && (
                            <div className="glass-card" style={{ padding: "1.2rem 1.5rem", marginBottom: 20 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                    <span style={{ display: "inline-block", width: 16, height: 16, border: "2px solid #4a8dc4", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                                    <span style={{ fontSize: "0.88rem", color: "#0b1f38", fontWeight: 500 }}>{loadingPhaseLabel}</span>
                                </div>
                                <p style={{ fontSize: "0.82rem", color: "#4a6a8a", margin: "6px 0 0", paddingLeft: 26 }}>{loadingPhaseDetail}</p>
                            </div>
                        )}

                        {/* Agent question card */}
                        {pendingQuestion && (
                            <div style={{ marginBottom: 20 }}>
                                <AgentQuestionCard
                                    question={pendingQuestion}
                                    onAnswer={handleAnswer}
                                    isSubmitting={submittingAnswer}
                                />
                            </div>
                        )}

                        {/* Agent action feed */}
                        <AgentActionFeed
                            actions={agentActions}
                            isActive={agentActive}
                            onStop={agentActive ? async () => {
                                wasCancelledRef.current = true;
                                setAgentActive(false);
                                setLoadingPhaseLabel("Stopping agent...");
                                setLoadingPhaseDetail("Waiting for the current step to finish.");
                                if (planRequestIdRef.current) {
                                    await cancelPlanningRequest(planRequestIdRef.current);
                                }
                            } : undefined}
                        />

                        {/* Scaffold review card */}
                        {scaffoldReady && scaffoldText && (
                            <div style={{ marginTop: 20 }}>
                                <ScaffoldReviewCard
                                    scaffoldText={scaffoldText}
                                    revisionCount={revisionCount}
                                    maxRevisions={1}
                                    feedback={scaffoldFeedback}
                                    onFeedbackChange={setScaffoldFeedback}
                                    onRevise={handleRevise}
                                    onApprove={handleExtract}
                                    isRevising={revisingScaffold}
                                    isExtracting={loading}
                                    scratchpad={agentScratchpad}
                                />
                            </div>
                        )}
                    </>
                )}
            </div>
        </>
    );
}

