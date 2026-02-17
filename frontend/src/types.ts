// NovaSync — shared TypeScript types

export interface ItineraryNode {
    title: string;
    activity_type: string;
    duration_mins: number | null;
    date_local: string | null;
    start_time_local: string | null;
    end_time_local: string | null;
    lat: number | null;
    long: number | null;
    description: string | null;
}

export interface ProcessIdeaResponse {
    trip_id: string;
    nodes: ItineraryNode[];
}

export interface InputDirectives {
    hard_constraints: string[];
    soft_preferences: string[];
    must_include: string[];
    avoid: string[];
    mobility_mode?: string | null;
    budget_level?: string | null;
    pace?: string | null;
}

export interface WorkerReport {
    worker_name: string;
    status: string;
    evidence_added: number;
    notes?: string | null;
}

export interface ValidationPlanActivity {
    title: string;
    activity_type: string;
    duration_mins: number | null;
    date_local: string | null;
    start_time_local: string | null;
    end_time_local: string | null;
    lat: number | null;
    long: number | null;
    description: string | null;
    order_index: number;
    source_evidence_ids: string[];
    validation_notes: string[];
}

export interface ValidationPlan {
    activities: ValidationPlanActivity[];
    warnings: string[];
    assumptions: string[];
}

export interface ValidationReport {
    errors: string[];
    warnings: string[];
    plan: ValidationPlan;
}

export interface EvidenceFacts {
    locations: string[];
    activities: string[];
    time_hints: string[];
    constraints: string[];
    vibe_tags: string[];
}

export interface EvidenceDebug {
    fetch_status?: string | null;
    page_title?: string | null;
    content_excerpt?: string | null;
    time_hint_sentences: string[];
    constraint_sentences: string[];
}

export interface EvidenceItem {
    id: string;
    source_type: string;
    source_ref: string;
    summary: string;
    facts: EvidenceFacts;
    confidence: number;
    citations: string[];
    raw_artifact_ref?: string | null;
    debug?: EvidenceDebug | null;
}

export interface ProcessIdeaDebugResponse extends ProcessIdeaResponse {
    worker_reports: WorkerReport[];
    validation_report: ValidationReport;
    evidence: EvidenceItem[];
}

export type ProcessIdeaApiResponse =
    | ProcessIdeaResponse
    | ProcessIdeaDebugResponse;
