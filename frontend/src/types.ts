// NovaSync — shared TypeScript types

export interface ItineraryNode {
    id?: string | null;            // DB-assigned UUID
    title: string;
    activity_type: string;
    duration_mins: number | null;
    date_local: string | null;
    start_time_local: string | null;
    end_time_local: string | null;
    lat: number | null;
    long: number | null;
    description: string | null;
    segment_origin?: "model" | "synthetic" | null;
    segment_kind?: "activity" | "transfer" | "buffer" | "rest" | null;
    for_travelers?: string[];
}

export interface ProcessIdeaResponse {
    trip_id: string;
    nodes: ItineraryNode[];
    planner_scaffold_text?: string | null;
}

export interface InputDirectives {
    hard_constraints: string[];
    soft_preferences: string[];
    must_include: string[];
    avoid: string[];
    mobility_mode?: string | null;
    budget_level?: string | null;
    pace?: string | null;
    // Lifestyle profile fields
    travel_party?: string[];
    dietary?: string[];
    wake_time_pref?: string | null;
    fitness_level?: string | null;
    accommodation_style?: string | null;
}

export interface LifestyleProfile {
    wake_time_pref: string | null;
    travel_party: string[];
    dietary: string[];
    fitness_level: string | null;
    accommodation_style: string | null;
    pace: string | null;
    mobility_mode: string | null;
    notes?: Record<string, string>;
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
    segment_origin?: "model" | "synthetic" | null;
    segment_kind?: "activity" | "transfer" | "buffer" | "rest" | null;
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
    parsed_text_preview?: string | null;
    raw_text_preview?: string | null;
    parsed_text_full?: string | null;
    raw_text_full?: string | null;
    llm_condensed_preview?: string | null;
    llm_condensed_full?: string | null;
    llm_summary_model?: string | null;
    llm_summary_error?: string | null;
    llm_summary_trace?: {
        enabled?: boolean;
        mode?: string | null;
        calls_total?: number;
        models_used?: string[];
        map_chunks_total?: number;
        map_chunks_succeeded?: number;
        map_chunks_failed?: number;
        reduce_called?: boolean;
        reduce_succeeded?: boolean;
        fallback_path?: string | null;
        reason?: string | null;
        input_chars_original?: number;
        input_chars_after_budget?: number;
        prompt_was_truncated?: boolean;
        config?: {
            map_reduce_enabled?: boolean;
            map_chunk_chars?: number;
            map_max_chunks?: number;
            map_workers?: number;
            map_timeout_seconds?: number;
            reduce_timeout_seconds?: number;
            single_pass_timeout_seconds?: number;
            input_max_chars?: number;
            output_max_chars?: number;
        };
        map_calls?: Array<{
            chunk_index?: number;
            status?: string;
            model?: string | null;
            elapsed_ms?: number;
            input_chars?: number;
            output_chars?: number;
            input_preview?: string;
            error?: string;
        }>;
        reduce_call?: {
            status?: string;
            model?: string | null;
            elapsed_ms?: number;
            input_chars?: number;
            output_chars?: number;
            input_preview?: string;
            error?: string;
        } | null;
        single_pass_call?: {
            status?: string;
            model?: string | null;
            elapsed_ms?: number;
            input_chars?: number;
            output_chars?: number;
            input_preview?: string;
            error?: string;
        } | null;
    } | null;
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

export interface UrlScraperDebugResponse {
    links: string[];
    evidence_count: number;
    elapsed_ms: number;
    evidence: EvidenceItem[];
}

export interface ProcessIdeaDebugResponse extends ProcessIdeaResponse {
    worker_reports: WorkerReport[];
    validation_report: ValidationReport;
    evidence: EvidenceItem[];
    debug_trace?: {
        qwen_media_signals?: Array<{
            source_filename: string | null;
            summary: string;
            location_cues: string[];
            activity_hints: string[];
            vibe_tags: string[];
            constraints: string[];
            confidence: number;
        }>;
        qwen_media_signal_count?: number;
        qwen_media_error?: string | null;
        planner_evidence_selected_ids?: string[];
        planner_evidence_selected?: EvidenceItem[];
        planner_evidence_lines?: string[];
        planner_evidence_budget?: {
            max_items: number;
            max_chars: number;
            line_max_chars: number;
            selected_items: number;
            selected_chars: number;
            total_available_items: number;
        };
        planner_prompt_chars?: number;
        planner_prompt_includes_scaffold?: boolean;
        planner_scaffold_prompt_chars?: number;
        planner_scaffold_text?: string | null;
        planner_scaffold_debug?: {
            scaffold_enabled?: boolean;
            scaffold_model_requested?: string;
            scaffold_models_attempted?: string[];
            scaffold_model_selected?: string | null;
            response_model?: string | null;
            temperature_used?: number | null;
            timeout_seconds?: number;
            prompt_chars_original?: number;
            prompt_chars_used?: number;
            prompt_was_truncated?: boolean;
            scaffold_chars?: number;
            scaffold_excerpt?: string | null;
            raw_response_excerpt?: string | null;
            error?: string | null;
            error_body?: string | null;
            attempts?: Array<{
                model: string;
                include_temperature: boolean;
                status: string;
                http_status?: number;
                error?: string;
                error_body_excerpt?: string;
                response_model?: string;
                scaffold_chars?: number;
            }>;
        };
        web_query_builder?: {
            query_source?: string;
            fallback_reason?: string | null;
            heuristic_queries?: string[];
            model_queries?: string[];
            queries_final?: string[];
            queries_executed?: string[];
            provider_requested?: string;
            provider_default?: string;
            tavily_key_present?: boolean;
            brave_key_present?: boolean;
            provider?: string;
            model_debug?: {
                query_builder_model?: string;
                query_builder_models_attempted?: string[];
                timeout_seconds?: number;
                idea_chars_original?: number;
                idea_chars_used?: number;
                idea_was_truncated?: boolean;
                temperature_used?: number | null;
                response_model?: string | null;
                raw_response_excerpt?: string | null;
                parsed_query_count?: number;
                sanitized_query_count?: number;
                error?: string | null;
                error_body?: string | null;
                attempts?: Array<{
                    model: string;
                    include_temperature: boolean;
                    status: string;
                    http_status?: number;
                    error?: string;
                    error_body_excerpt?: string;
                    sanitized_query_count?: number;
                    response_model?: string;
                }>;
            };
            query_outcomes?: Array<{
                query: string;
                provider: string;
                result_count: number;
                error?: string | null;
                citations: string[];
                snippet_digest?: string;
                results_preview?: Array<{
                    url: string;
                    title: string;
                    snippet: string;
                }>;
            }>;
        };
    };
}

export type ProcessIdeaApiResponse =
    | ProcessIdeaResponse
    | ProcessIdeaDebugResponse;

// ── Booking types ──────────────────────────────────────────────────────────────

export interface BookingStartRequest {
    restaurant_name: string;
    city: string;
    date: string;       // YYYY-MM-DD
    time: string;       // HH:MM
    party_size: number;
}

export interface BookingResumeData {
    phone?: string;
    email?: string;
    password?: string;
    notes?: string;
}

export interface BookingSession {
    session_id: string;
}

export interface BookingNeedsUserInputEvent {
    fields: string[];   // e.g. ["phone"] or ["manual_required"]
}

// ── Agent action events (real-time tool calls during planning) ───────────────
export interface AgentActionEvent {
    tool_name:
        | "search_activities"
        | "get_local_events"
        | "get_weather"
        | "validate_place"
        | "write_to_scratchpad"
        | "self_critique_plan"
        | "ask_user"
        | "finalize_plan";
    summary: string;
    reasoning?: string;
    result_preview?: string;
    tool_input?: Record<string, unknown>;
    elapsed_ms?: number;
    iteration?: number;
    scratchpad?: string;
}

// ── Human-in-the-loop scaffold review ────────────────────────────────────────
export interface ScaffoldReadyEvent {
    session_id: string;
    scaffold_text: string;
    revision_count: number;
    max_revisions: number;
    scratchpad?: string;  // Agent's planning notes
}

// ── Agent action events (real-time tool calls during planning) ───────────────
export interface AgentActionEvent {
    tool_name:
        | "search_activities"
        | "get_local_events"
        | "get_weather"
        | "validate_place"
        | "write_to_scratchpad"
        | "self_critique_plan"
        | "ask_user"
        | "finalize_plan";
    summary: string;
    reasoning?: string;
    result_preview?: string;
    tool_input?: Record<string, unknown>;  // full input for debugging
    elapsed_ms?: number;                   // tool execution time in ms
    iteration?: number;                    // which agent iteration called this
    scratchpad?: string;                   // current agent scratchpad state
}

// ── Agent question events (ask_user tool) ────────────────────────────────────
export interface AgentQuestionOption {
    id: string;
    label: string;
}

export interface AgentQuestionEvent {
    request_id: string;   // key for answer endpoint
    question_id: string;
    question: string;
    options: AgentQuestionOption[];
}

// ── Booking HITL auth pause ───────────────────────────────────────────────────
export interface NeedsAuthEvent {
    type: "needs_auth";
    message: string;
    auth_url?: string | null;
}

// ── Booking HITL course review pause ─────────────────────────────────────────
export interface NeedsCourseReviewEvent {
    type: "needs_course_review";
    message: string;
    summary: string;
}

// ── Group trip types ──────────────────────────────────────────────────────────
export interface GroupTrip {
    id: string;
    name: string;
    trip_location?: string;
    start_date?: string;
    end_date?: string;
    trip_days?: number;
    trip_type: "individual" | "group";
    max_travelers: number;
    created_at?: string;
}

export interface TravelerParticipant {
    nickname: string;
    submitted_at: string;
    free_text?: string | null;
    input_directives?: {
        budget_level?: string | null;
        pace?: string | null;
        dietary?: string[];
        wake_time_pref?: string | null;
        must_include?: string[];
        avoid?: string[];
        hard_constraints?: string[];
        soft_preferences?: string[];
        inspiration_links?: string[];
        [key: string]: unknown;
    } | null;
}

export interface GroupPlanStatusResponse {
    group_id: string;
    destination?: string;
    participants: TravelerParticipant[];
    slots_remaining?: number;
}

export interface ConflictItem {
    field: string;
    values: string[];
    travelers: string[];
}

// Alias for backwards compatibility
export type GroupPlanStatus = GroupPlanStatusResponse;

export interface InputDirectivesGroup {
    hard_constraints: string[];
    soft_preferences: string[];
    must_include: string[];
    avoid: string[];
    mobility_mode?: string;
    budget_level?: string;
    pace?: string;
    travel_party: string[];
    dietary: string[];
    wake_time_pref?: string;
    fitness_level?: string;
    accommodation_style?: string;
}
