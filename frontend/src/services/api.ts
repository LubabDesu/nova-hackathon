// NovaSync — API service layer

import type { InputDirectives, ProcessIdeaApiResponse } from "../types";

const API_BASE = "http://localhost:8000/api";

interface ProcessIdeaOptions {
    tripId?: string;
    tripLocation?: string;
    startDate?: string;
    endDate?: string;
    files?: File[];
    links?: string[];
    inputDirectives?: InputDirectives;
    debug?: boolean;
}

export async function processIdea(
    idea: string,
    options: ProcessIdeaOptions = {},
): Promise<ProcessIdeaApiResponse> {
    const {
        tripId,
        tripLocation,
        startDate,
        endDate,
        files = [],
        links = [],
        inputDirectives = {
            hard_constraints: [],
            soft_preferences: [],
            must_include: [],
            avoid: [],
        },
        debug = false,
    } = options;

    const formData = new FormData();
    formData.append("idea", idea);
    formData.append("links", JSON.stringify(links));
    formData.append("input_directives", JSON.stringify(inputDirectives));

    if (tripId) {
        formData.append("trip_id", tripId);
    }
    if (tripLocation) {
        formData.append("trip_location", tripLocation);
    }
    if (startDate) {
        formData.append("start_date", startDate);
    }
    if (endDate) {
        formData.append("end_date", endDate);
    }

    for (const file of files) {
        formData.append("files", file);
    }

    const url = new URL(`${API_BASE}/process-idea`);
    if (debug) {
        url.searchParams.set("debug", "true");
    }

    const res = await fetch(url.toString(), {
        method: "POST",
        body: formData,
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? "Request failed");
    }

    return res.json() as Promise<ProcessIdeaApiResponse>;
}
