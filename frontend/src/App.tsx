// NovaSync — main application shell

import { useEffect, useRef, useState, type FormEvent } from "react";
import {
    getCountries,
    getStatesOfCountry,
    type ICountry,
    type IState,
} from "@countrystatecity/countries";
import IdeaInput from "./components/IdeaInput";
import ResultDisplay from "./components/ResultDisplay";
import LifestyleModal, {
    loadLifestyleProfile,
    saveLifestyleProfile,
} from "./components/LifestyleModal";
import { lifestyleToDirectives } from "./utils/lifestyleToDirectives";
import { debugUrlScraper, planIdeaStream, reviseScaffold, extractIdeaStream } from "./services/api";
import type {
    InputDirectives,
    ItineraryNode,
    LifestyleProfile,
    ProcessIdeaDebugResponse,
    UrlScraperDebugResponse,
} from "./types";
import "./App.css";
import IdeaDropzone from "./components/IdeaDropZone";
import ScaffoldReviewCard from "./components/ScaffoldReviewCard";

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
    "../node_modules/@countrystatecity/countries/dist/data/countries.json",
);
const stateDataLoaders = import.meta.glob<{ default: IState[] }>(
    "../node_modules/@countrystatecity/countries/dist/data/*/states.json",
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

export default function App() {
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
            setLoadingPhaseLabel("Draft plan ready");
            setLoadingPhaseDetail("Review and approve your plan to generate the full itinerary.");
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Something went wrong");
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
        if (!sessionId) return;

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
            setNodes(res.nodes);
            setTripId(res.trip_id);
            if (debugMode) {
                setDebugPayload(res as ProcessIdeaDebugResponse);
            }
            setPendingFiles([]);
            setPendingLinks([]);
            setLinkInput("");
            setLinkError(null);
            setSessionId(null);
        } catch (err: unknown) {
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

    return (
        <div className="app-shell">
            {showLifestyleModal && (
                <LifestyleModal
                    onConfirm={handleLifestyleConfirm}
                    onSkip={handleLifestyleSkip}
                />
            )}
            <header className="app-header">
                <div className="logo">
                    <span className="logo-icon">◆</span> NovaSync
                </div>
                <span className="header-tag">Amazon Nova Hackathon</span>
            </header>
            <main className="app-main">
                <section className="left-panel">
                    <div className="dropzone-panel">
                        <h2 className="zone-title">
                            <span className="zone-icon">📎</span> Idea Drop Zone
                        </h2>
                        <p className="zone-subtitle">
                            Drop screenshots or short videos here, then use the
                            chat input below to describe your trip.
                        </p>
                        <IdeaDropzone onFilesAdded={handleFilesAdded} />
                        <form className="link-input-row" onSubmit={handleAddLink}>
                            <input
                                type="url"
                                className="link-input"
                                value={linkInput}
                                onChange={(event) => {
                                    setLinkInput(event.target.value);
                                    if (linkError) {
                                        setLinkError(null);
                                    }
                                }}
                                placeholder="Paste an Instagram, TikTok, or web link..."
                                disabled={loading}
                            />
                            <button
                                type="submit"
                                className="link-add-btn"
                                disabled={loading || linkInput.trim().length === 0}
                            >
                                Add
                            </button>
                        </form>
                        {linkError && (
                            <p className="link-input-error">{linkError}</p>
                        )}
                        <div className="staging-area">
                            <div className="staging-header">
                                <h3>Staging Area</h3>
                                <div className="staging-header-actions">
                                    <span>
                                        {pendingFiles.length + pendingLinks.length}{" "}
                                        item
                                        {pendingFiles.length + pendingLinks.length !==
                                        1
                                            ? "s"
                                            : ""}
                                    </span>
                                    <button
                                        type="button"
                                        className="staging-test-btn"
                                        onClick={handleDebugUrlScraper}
                                        disabled={urlScraperLoading || pendingLinks.length === 0}
                                    >
                                        {urlScraperLoading
                                            ? "Testing..."
                                            : "Test URL Scraper"}
                                    </button>
                                </div>
                            </div>
                            {pendingFiles.length === 0 &&
                            pendingLinks.length === 0 ? (
                                <p className="staging-empty">
                                    Add files or links to include extra context.
                                </p>
                            ) : (
                                <div className="staging-tags">
                                    {pendingFiles.map((file, index) => (
                                        <span
                                            className="staging-pill"
                                            key={`${file.name}-${file.lastModified}-${index}`}
                                        >
                                            <span className="staging-pill-label">
                                                📁 {file.name}
                                            </span>
                                            <button
                                                type="button"
                                                className="staging-pill-remove"
                                                onClick={() =>
                                                    removePendingFile(index)
                                                }
                                                aria-label={`Remove ${file.name}`}
                                            >
                                                x
                                            </button>
                                        </span>
                                    ))}
                                    {pendingLinks.map((link, index) => (
                                        <span
                                            className="staging-pill is-link"
                                            key={`${link}-${index}`}
                                        >
                                            <span className="staging-pill-label">
                                                🔗 {link}
                                            </span>
                                            <button
                                                type="button"
                                                className="staging-pill-remove"
                                                onClick={() =>
                                                    removePendingLink(index)
                                                }
                                                aria-label={`Remove ${link}`}
                                            >
                                                x
                                            </button>
                                        </span>
                                    ))}
                                </div>
                            )}
                            {urlScraperError && (
                                <p className="staging-test-error">{urlScraperError}</p>
                            )}
                            {urlScraperResult && (
                                <div className="staging-test-result">
                                    <p className="staging-test-summary">
                                        URL scraper returned {urlScraperResult.evidence_count} evidence item(s) in{" "}
                                        {(urlScraperResult.elapsed_ms / 1000).toFixed(2)}s.
                                    </p>
                                    <div className="staging-test-list">
                                        {urlScraperResult.evidence.map((item) => (
                                            <article
                                                className="staging-test-card"
                                                key={item.id}
                                            >
                                                <p className="staging-test-title">
                                                    {item.source_ref}
                                                </p>
                                                <p className="staging-test-meta">
                                                    type={item.source_type} | conf=
                                                    {Math.round(item.confidence * 100)}% | fetch=
                                                    {item.debug?.fetch_status ?? "n/a"}
                                                </p>
                                                <p className="staging-test-summary-line">
                                                    {item.summary}
                                                </p>
                                                {item.debug?.page_title && (
                                                    <p className="staging-test-summary-line">
                                                        title: {item.debug.page_title}
                                                    </p>
                                                )}
                                                {item.facts.locations.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>locations:</strong>{" "}
                                                        {item.facts.locations.join(" | ")}
                                                    </p>
                                                )}
                                                {item.facts.activities.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>activities:</strong>{" "}
                                                        {item.facts.activities.join(" | ")}
                                                    </p>
                                                )}
                                                {item.facts.time_hints.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>time_hints:</strong>{" "}
                                                        {item.facts.time_hints.join(" | ")}
                                                    </p>
                                                )}
                                                {item.facts.constraints.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>constraints:</strong>{" "}
                                                        {item.facts.constraints.join(" | ")}
                                                    </p>
                                                )}
                                                {item.facts.vibe_tags.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>vibe_tags:</strong>{" "}
                                                        {item.facts.vibe_tags.join(" | ")}
                                                    </p>
                                                )}
                                                {item.debug?.time_hint_sentences &&
                                                    item.debug.time_hint_sentences.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>time_hint_sentences:</strong>{" "}
                                                        {item.debug.time_hint_sentences.join(" | ")}
                                                    </p>
                                                )}
                                                {item.debug?.constraint_sentences &&
                                                    item.debug.constraint_sentences.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>constraint_sentences:</strong>{" "}
                                                        {item.debug.constraint_sentences.join(" | ")}
                                                    </p>
                                                )}
                                                {item.citations.length > 0 && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>citations:</strong>{" "}
                                                        {item.citations.join(" | ")}
                                                    </p>
                                                )}
                                                {item.debug?.llm_summary_model && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>llm_summary_model:</strong>{" "}
                                                        {item.debug.llm_summary_model}
                                                    </p>
                                                )}
                                                {item.debug?.llm_summary_trace && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>llm_call_trace:</strong>{" "}
                                                        mode=
                                                        {item.debug.llm_summary_trace.mode ?? "unknown"} | calls=
                                                        {item.debug.llm_summary_trace.calls_total ?? 0}
                                                        {item.debug.llm_summary_trace.models_used &&
                                                            item.debug.llm_summary_trace.models_used.length > 0 &&
                                                            ` | models=${item.debug.llm_summary_trace.models_used.join(", ")}`}
                                                        {typeof item.debug.llm_summary_trace.map_chunks_total ===
                                                            "number" &&
                                                            ` | map_chunks=${item.debug.llm_summary_trace.map_chunks_succeeded ?? 0}/${item.debug.llm_summary_trace.map_chunks_total}`}
                                                    </p>
                                                )}
                                                {item.debug?.llm_summary_error && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>llm_summary_error:</strong>{" "}
                                                        {item.debug.llm_summary_error}
                                                    </p>
                                                )}
                                                {item.debug?.llm_condensed_preview?.trim() && (
                                                    <p className="staging-test-summary-line">
                                                        <strong>llm_condensed_preview:</strong>{" "}
                                                        {item.debug.llm_condensed_preview}
                                                    </p>
                                                )}
                                                {item.debug?.llm_condensed_full?.trim() && (
                                                    <details className="staging-test-details">
                                                        <summary>llm_condensed_full</summary>
                                                        <pre className="staging-test-pre">
                                                            {item.debug.llm_condensed_full}
                                                        </pre>
                                                    </details>
                                                )}
                                                {item.debug?.llm_summary_trace && (
                                                    <details className="staging-test-details" open>
                                                        <summary>map-reduce trace</summary>
                                                        <div className="mr-trace">
                                                            <div className="mr-trace-header">
                                                                <span className="mr-mode">mode: <strong>{item.debug.llm_summary_trace.mode ?? "unknown"}</strong></span>
                                                                <span className="mr-calls">LLM calls: <strong>{item.debug.llm_summary_trace.calls_total ?? 0}</strong></span>
                                                                {typeof item.debug.llm_summary_trace.map_chunks_total === "number" && (
                                                                    <span className={`mr-chunks ${item.debug.llm_summary_trace.map_chunks_failed ? "mr-chunks--warn" : "mr-chunks--ok"}`}>
                                                                        map chunks: <strong>{item.debug.llm_summary_trace.map_chunks_succeeded ?? 0}/{item.debug.llm_summary_trace.map_chunks_total}</strong> ok
                                                                        {item.debug.llm_summary_trace.map_chunks_failed ? ` · ${item.debug.llm_summary_trace.map_chunks_failed} failed` : ""}
                                                                    </span>
                                                                )}
                                                                {item.debug.llm_summary_trace.reduce_called && (
                                                                    <span className={`mr-reduce ${item.debug.llm_summary_trace.reduce_succeeded ? "mr-reduce--ok" : "mr-reduce--warn"}`}>
                                                                        reduce: <strong>{item.debug.llm_summary_trace.reduce_succeeded ? "✓" : "✗"}</strong>
                                                                    </span>
                                                                )}
                                                            </div>
                                                            {item.debug.llm_summary_trace.map_calls && item.debug.llm_summary_trace.map_calls.length > 0 && (() => {
                                                                const calls = item.debug.llm_summary_trace.map_calls!;
                                                                const maxMs = Math.max(...calls.map(c => c.elapsed_ms ?? 0), 1);
                                                                const totalSeqMs = calls.reduce((s, c) => s + (c.elapsed_ms ?? 0), 0);
                                                                return (
                                                                    <div className="mr-chart">
                                                                        <div className="mr-chart-label">parallel map phase (each row = 1 chunk, width = latency)</div>
                                                                        {calls.map((call) => (
                                                                            <div key={call.chunk_index} className="mr-bar-row">
                                                                                <span className="mr-bar-label">#{call.chunk_index}</span>
                                                                                <div className="mr-bar-track">
                                                                                    <div
                                                                                        className={`mr-bar ${call.status === "ok" ? "mr-bar--ok" : "mr-bar--err"}`}
                                                                                        style={{ width: `${Math.max(2, ((call.elapsed_ms ?? 0) / maxMs) * 100)}%` }}
                                                                                        title={`chunk ${call.chunk_index}: ${call.elapsed_ms?.toFixed(0)}ms · ${call.input_chars} chars · ${call.status}`}
                                                                                    />
                                                                                </div>
                                                                                <span className="mr-bar-ms">{call.elapsed_ms?.toFixed(0)}ms</span>
                                                                                {call.status !== "ok" && <span className="mr-bar-err-label">{call.error}</span>}
                                                                            </div>
                                                                        ))}
                                                                        <div className="mr-timing">
                                                                            <span>slowest chunk: <strong>{maxMs.toFixed(0)}ms</strong></span>
                                                                            <span>sum if sequential: <strong>{totalSeqMs.toFixed(0)}ms</strong></span>
                                                                            <span className="mr-speedup">parallel speedup: ~<strong>{(totalSeqMs / maxMs).toFixed(1)}×</strong></span>
                                                                        </div>
                                                                    </div>
                                                                );
                                                            })()}
                                                            {item.debug.llm_summary_trace.reduce_call && (
                                                                <div className="mr-reduce-row">
                                                                    <span>reduce call: </span>
                                                                    <span className={item.debug.llm_summary_trace.reduce_call.status === "ok" ? "mr-reduce--ok" : "mr-reduce--warn"}>
                                                                        {item.debug.llm_summary_trace.reduce_call.status} · {item.debug.llm_summary_trace.reduce_call.elapsed_ms?.toFixed(0)}ms · {item.debug.llm_summary_trace.reduce_call.input_chars} chars in
                                                                    </span>
                                                                </div>
                                                            )}
                                                            {item.debug.llm_summary_trace.single_pass_call && (
                                                                <div className="mr-reduce-row">
                                                                    <span>single-pass call: </span>
                                                                    <span className={item.debug.llm_summary_trace.single_pass_call.status === "ok" ? "mr-reduce--ok" : "mr-reduce--warn"}>
                                                                        {item.debug.llm_summary_trace.single_pass_call.status} · {item.debug.llm_summary_trace.single_pass_call.elapsed_ms?.toFixed(0)}ms
                                                                    </span>
                                                                </div>
                                                            )}
                                                            {item.debug.llm_summary_trace.fallback_path && (
                                                                <div className="mr-fallback">fallback path: {item.debug.llm_summary_trace.fallback_path}</div>
                                                            )}
                                                        </div>
                                                    </details>
                                                )}
                                                <p className="staging-test-summary-line">
                                                    <strong>parsed_text_preview:</strong>{" "}
                                                    {item.debug?.parsed_text_preview?.trim()
                                                        ? item.debug.parsed_text_preview
                                                        : "No parsed page text available (fetch likely failed, blocked, or unsupported content type)."}
                                                </p>
                                                <details className="staging-test-details">
                                                    <summary>raw_text_preview (pre-filter)</summary>
                                                    <p className="staging-test-summary-line">
                                                        {item.debug?.raw_text_preview?.trim()
                                                            ? item.debug.raw_text_preview
                                                            : "No raw text preview available."}
                                                    </p>
                                                </details>
                                                <details className="staging-test-details" open>
                                                    <summary>parsed_text_full (untruncated debug)</summary>
                                                    <pre className="staging-test-pre">
                                                        {item.debug?.parsed_text_full?.trim()
                                                            ? item.debug.parsed_text_full
                                                            : "No parsed full text available."}
                                                    </pre>
                                                </details>
                                                <details className="staging-test-details">
                                                    <summary>raw_text_full (untruncated debug)</summary>
                                                    <pre className="staging-test-pre">
                                                        {item.debug?.raw_text_full?.trim()
                                                            ? item.debug.raw_text_full
                                                            : "No raw full text available."}
                                                    </pre>
                                                </details>
                                                {item.debug?.content_excerpt && (
                                                    <details className="staging-test-details">
                                                        <summary>content_excerpt</summary>
                                                        <p className="staging-test-summary-line">
                                                            {item.debug.content_excerpt}
                                                        </p>
                                                    </details>
                                                )}
                                            </article>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                        <div className="directives-panel">
                            <h3 className="directives-title">Trip Context</h3>
                            <p className="directives-hint">
                                Choose fixed dates or set not decided with trip length.
                            </p>
                            <div className="trip-context-grid">
                                <label className="directive-field">
                                    <span>Country</span>
                                    <input
                                        type="text"
                                        list="country-options"
                                        value={countryNameInput}
                                        onChange={(event) =>
                                            handleCountryNameChange(
                                                event.target.value,
                                            )
                                        }
                                        disabled={loading || countriesLoading}
                                        placeholder={
                                            countriesLoading
                                                ? "Loading countries..."
                                                : "Type to search country (optional)"
                                        }
                                    />
                                    <datalist id="country-options">
                                        {countries.map((country) => (
                                            <option
                                                key={country.iso2}
                                                value={country.name}
                                                label={`${country.name} ${country.emoji}`}
                                            >
                                                {country.emoji} {country.name}
                                            </option>
                                        ))}
                                    </datalist>
                                </label>
                                <label className="directive-field">
                                    <span>State / Region</span>
                                    <input
                                        type="text"
                                        list="state-options"
                                        value={stateNameInput}
                                        onChange={(event) =>
                                            handleStateNameChange(
                                                event.target.value,
                                            )
                                        }
                                        disabled={
                                            loading ||
                                            !countryCodeInput ||
                                            statesLoading
                                        }
                                        placeholder={
                                            !countryCodeInput
                                                ? "Choose a country first"
                                                : statesLoading
                                                  ? "Loading states..."
                                                  : "Type to search state/region (optional)"
                                        }
                                    />
                                    <datalist id="state-options">
                                        {states.map((state) => (
                                            <option
                                                key={state.iso2}
                                                value={state.name}
                                            >
                                                {state.name}
                                            </option>
                                        ))}
                                    </datalist>
                                </label>
                                <label className="directive-field">
                                    <span>Date planning</span>
                                    <select
                                        value={tripWindowMode}
                                        onChange={(event) =>
                                            setTripWindowMode(
                                                event.target.value as
                                                    | "fixed"
                                                    | "not_decided",
                                            )
                                        }
                                        disabled={loading}
                                    >
                                        <option value="fixed">I know exact dates</option>
                                        <option value="not_decided">Not decided yet</option>
                                    </select>
                                </label>
                                {tripWindowMode === "fixed" ? (
                                    <>
                                        <label className="directive-field">
                                            <span>Start date</span>
                                            <input
                                                type="date"
                                                value={startDateInput}
                                                onChange={(event) =>
                                                    setStartDateInput(event.target.value)
                                                }
                                                disabled={loading}
                                                required
                                            />
                                        </label>
                                        <label className="directive-field">
                                            <span>End date</span>
                                            <input
                                                type="date"
                                                value={endDateInput}
                                                onChange={(event) =>
                                                    setEndDateInput(event.target.value)
                                                }
                                                disabled={loading}
                                                required
                                            />
                                        </label>
                                    </>
                                ) : (
                                    <label className="directive-field">
                                        <span>Trip length (days)</span>
                                        <input
                                            type="number"
                                            min={1}
                                            max={60}
                                            step={1}
                                            value={tripDaysInput}
                                            onChange={(event) =>
                                                setTripDaysInput(event.target.value)
                                            }
                                            placeholder="5"
                                            disabled={loading}
                                            required
                                        />
                                    </label>
                                )}
                            </div>
                        </div>
                        <div className="directives-panel">
                            <h3 className="directives-title">
                                Planning Directives
                            </h3>
                            <p className="directives-hint">
                                Add one item per line to steer the planner.
                            </p>
                            <div className="directives-grid">
                                <label className="directive-field">
                                    <span>Hard constraints</span>
                                    <textarea
                                        value={hardConstraintsInput}
                                        onChange={(event) =>
                                            setHardConstraintsInput(
                                                event.target.value,
                                            )
                                        }
                                        rows={2}
                                        placeholder="Only drive before sunset"
                                        disabled={loading}
                                    />
                                </label>
                                <label className="directive-field">
                                    <span>Soft preferences</span>
                                    <textarea
                                        value={softPreferencesInput}
                                        onChange={(event) =>
                                            setSoftPreferencesInput(
                                                event.target.value,
                                            )
                                        }
                                        rows={2}
                                        placeholder="Prefer scenic routes"
                                        disabled={loading}
                                    />
                                </label>
                                <label className="directive-field">
                                    <span>Must include</span>
                                    <textarea
                                        value={mustIncludeInput}
                                        onChange={(event) =>
                                            setMustIncludeInput(
                                                event.target.value,
                                            )
                                        }
                                        rows={2}
                                        placeholder="MONA"
                                        disabled={loading}
                                    />
                                </label>
                                <label className="directive-field">
                                    <span>Avoid</span>
                                    <textarea
                                        value={avoidInput}
                                        onChange={(event) =>
                                            setAvoidInput(event.target.value)
                                        }
                                        rows={2}
                                        placeholder="Late-night driving"
                                        disabled={loading}
                                    />
                                </label>
                            </div>
                            <label className="debug-toggle">
                                <input
                                    type="checkbox"
                                    checked={debugMode}
                                    onChange={(event) =>
                                        setDebugMode(event.target.checked)
                                    }
                                    disabled={loading}
                                />
                                Include backend debug payload
                            </label>
                        </div>
                    </div>
                    <IdeaInput
                        onSubmit={handlePlanRequest}
                        loading={loading}
                        loadingPhaseLabel={loadingPhaseLabel}
                        loadingPhaseDetail={loadingPhaseDetail}
                        loadingStepIndex={loadingPhaseIndex}
                        loadingTotalSteps={LOADING_PHASES.length}
                    />
                </section>
                <section className="right-panel">
                    {scaffoldReady && scaffoldText ? (
                        <div className="result-zone">
                            <ScaffoldReviewCard
                                scaffoldText={scaffoldText}
                                revisionCount={revisionCount}
                                maxRevisions={1}
                                feedback={scaffoldFeedback}
                                onFeedbackChange={setScaffoldFeedback}
                                onRevise={handleRevise}
                                onApprove={handleExtract}
                                isRevising={revisingScaffold}
                            />
                        </div>
                    ) : (
                        <ResultDisplay
                            nodes={nodes}
                            tripId={tripId}
                            onNodesChange={(updated) => setNodes(updated)}
                            plannerReasoning={plannerReasoning}
                            error={error}
                            debugPayload={debugPayload}
                            loading={loading}
                            loadingPhaseLabel={loadingPhaseLabel}
                            loadingPhaseDetail={loadingPhaseDetail}
                            loadingStepIndex={loadingPhaseIndex}
                            loadingTotalSteps={LOADING_PHASES.length}
                        />
                    )}
                </section>
            </main>
        </div>
    );
}
