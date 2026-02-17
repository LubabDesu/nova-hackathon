// NovaSync — main application shell

import { useEffect, useState, type FormEvent } from "react";
import {
    getCitiesOfState,
    getCountries,
    getStatesOfCountry,
    type ICity,
    type ICountry,
    type IState,
} from "@countrystatecity/countries";
import IdeaInput from "./components/IdeaInput";
import ResultDisplay from "./components/ResultDisplay";
import { processIdea } from "./services/api";
import type {
    InputDirectives,
    ItineraryNode,
    ProcessIdeaDebugResponse,
} from "./types";
import "./App.css";
import IdeaDropzone from "./components/IdeaDropZone";

const sortByName = <T extends { name: string }>(a: T, b: T) =>
    a.name.localeCompare(b.name);

export default function App() {
    const [nodes, setNodes] = useState<ItineraryNode[]>([]);
    const [tripId, setTripId] = useState<string | null>(null);
    const [pendingFiles, setPendingFiles] = useState<File[]>([]);
    const [pendingLinks, setPendingLinks] = useState<string[]>([]);
    const [linkInput, setLinkInput] = useState("");
    const [linkError, setLinkError] = useState<string | null>(null);
    const [countries, setCountries] = useState<ICountry[]>([]);
    const [states, setStates] = useState<IState[]>([]);
    const [cities, setCities] = useState<ICity[]>([]);
    const [countryCodeInput, setCountryCodeInput] = useState("");
    const [stateCodeInput, setStateCodeInput] = useState("");
    const [cityInput, setCityInput] = useState("");
    const [countriesLoading, setCountriesLoading] = useState(false);
    const [statesLoading, setStatesLoading] = useState(false);
    const [citiesLoading, setCitiesLoading] = useState(false);
    const [startDateInput, setStartDateInput] = useState("");
    const [endDateInput, setEndDateInput] = useState("");
    const [hardConstraintsInput, setHardConstraintsInput] = useState("");
    const [softPreferencesInput, setSoftPreferencesInput] = useState("");
    const [mustIncludeInput, setMustIncludeInput] = useState("");
    const [avoidInput, setAvoidInput] = useState("");
    const [debugMode, setDebugMode] = useState(true);
    const [debugPayload, setDebugPayload] =
        useState<ProcessIdeaDebugResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const parseLines = (value: string) =>
        value
            .split("\n")
            .map((line) => line.trim())
            .filter((line) => line.length > 0);

    const selectedCountry =
        countries.find((country) => country.iso2 === countryCodeInput) ?? null;
    const selectedState =
        states.find((state) => state.iso2 === stateCodeInput) ?? null;
    const selectedCity =
        cities.find((city) => city.name === cityInput) ?? null;
    const resolvedTripLocation =
        selectedCountry && selectedState && selectedCity
            ? `${selectedCity.name}, ${selectedState.name}, ${selectedCountry.name}`
            : selectedCountry && selectedState
              ? `${selectedState.name}, ${selectedCountry.name}`
              : selectedCountry?.name || undefined;

    useEffect(() => {
        let cancelled = false;

        const loadCountries = async () => {
            setCountriesLoading(true);
            try {
                const fetchedCountries = await getCountries();
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
            setCities([]);
            setCityInput("");
            return () => {
                cancelled = true;
            };
        }

        const loadStates = async () => {
            setStatesLoading(true);
            try {
                const fetchedStates = await getStatesOfCountry(countryCodeInput);
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
        setCities([]);
        setCityInput("");
        void loadStates();

        return () => {
            cancelled = true;
        };
    }, [countryCodeInput]);

    useEffect(() => {
        let cancelled = false;

        if (!countryCodeInput || !stateCodeInput) {
            setCities([]);
            setCityInput("");
            return () => {
                cancelled = true;
            };
        }

        const loadCities = async () => {
            setCitiesLoading(true);
            try {
                const fetchedCities = await getCitiesOfState(
                    countryCodeInput,
                    stateCodeInput,
                );
                if (!cancelled) {
                    setCities([...fetchedCities].sort(sortByName));
                }
            } catch (fetchError) {
                console.error("Failed to fetch cities", fetchError);
                if (!cancelled) {
                    setCities([]);
                }
            } finally {
                if (!cancelled) {
                    setCitiesLoading(false);
                }
            }
        };

        setCityInput("");
        void loadCities();

        return () => {
            cancelled = true;
        };
    }, [countryCodeInput, stateCodeInput]);

    const handleSubmit = async (idea: string) => {
        setLoading(true);
        setError(null);
        setDebugPayload(null);

        const inputDirectives: InputDirectives = {
            hard_constraints: parseLines(hardConstraintsInput),
            soft_preferences: parseLines(softPreferencesInput),
            must_include: parseLines(mustIncludeInput),
            avoid: parseLines(avoidInput),
        };

        if (startDateInput && endDateInput && startDateInput > endDateInput) {
            setError("Start date must be earlier than or equal to end date.");
            setLoading(false);
            return;
        }

        try {
            const res = await processIdea(idea, {
                tripId: tripId ?? undefined,
                tripLocation: resolvedTripLocation,
                startDate: startDateInput || undefined,
                endDate: endDateInput || undefined,
                files: pendingFiles,
                links: pendingLinks,
                inputDirectives,
                debug: debugMode,
            });
            setNodes(res.nodes);
            setTripId(res.trip_id);
            if ("worker_reports" in res && "validation_report" in res) {
                setDebugPayload(res as ProcessIdeaDebugResponse);
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

    return (
        <div className="app-shell">
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
                                <span>
                                    {pendingFiles.length + pendingLinks.length}{" "}
                                    item
                                    {pendingFiles.length + pendingLinks.length !==
                                    1
                                        ? "s"
                                        : ""}
                                </span>
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
                        </div>
                        <div className="directives-panel">
                            <h3 className="directives-title">Trip Context</h3>
                            <p className="directives-hint">
                                Optional trip window and location details.
                            </p>
                            <div className="trip-context-grid">
                                <label className="directive-field">
                                    <span>Country</span>
                                    <select
                                        value={countryCodeInput}
                                        onChange={(event) =>
                                            setCountryCodeInput(
                                                event.target.value,
                                            )
                                        }
                                        disabled={loading || countriesLoading}
                                    >
                                        <option value="">
                                            {countriesLoading
                                                ? "Loading countries..."
                                                : "Select a country (optional)"}
                                        </option>
                                        {countries.map((country) => (
                                            <option
                                                key={country.iso2}
                                                value={country.iso2}
                                            >
                                                {country.emoji} {country.name}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <label className="directive-field">
                                    <span>State / Region</span>
                                    <select
                                        value={stateCodeInput}
                                        onChange={(event) =>
                                            setStateCodeInput(
                                                event.target.value,
                                            )
                                        }
                                        disabled={
                                            loading ||
                                            !countryCodeInput ||
                                            statesLoading
                                        }
                                    >
                                        <option value="">
                                            {!countryCodeInput
                                                ? "Choose a country first"
                                                : statesLoading
                                                  ? "Loading states..."
                                                  : "Select a state/region (optional)"}
                                        </option>
                                        {states.map((state) => (
                                            <option
                                                key={state.iso2}
                                                value={state.iso2}
                                            >
                                                {state.name}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <label className="directive-field">
                                    <span>City</span>
                                    <select
                                        value={cityInput}
                                        onChange={(event) =>
                                            setCityInput(event.target.value)
                                        }
                                        disabled={
                                            loading ||
                                            !countryCodeInput ||
                                            !stateCodeInput ||
                                            citiesLoading
                                        }
                                    >
                                        <option value="">
                                            {!countryCodeInput || !stateCodeInput
                                                ? "Choose country + state first"
                                                : citiesLoading
                                                  ? "Loading cities..."
                                                  : "Select a city (optional)"}
                                        </option>
                                        {cities.map((city) => (
                                            <option
                                                key={city.id}
                                                value={city.name}
                                            >
                                                {city.name}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                                <label className="directive-field">
                                    <span>Start date</span>
                                    <input
                                        type="date"
                                        value={startDateInput}
                                        onChange={(event) =>
                                            setStartDateInput(event.target.value)
                                        }
                                        disabled={loading}
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
                                    />
                                </label>
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
                    <IdeaInput onSubmit={handleSubmit} loading={loading} />
                </section>
                <section className="right-panel">
                    <ResultDisplay
                        nodes={nodes}
                        tripId={tripId}
                        error={error}
                        debugPayload={debugPayload}
                    />
                </section>
            </main>
        </div>
    );
}
