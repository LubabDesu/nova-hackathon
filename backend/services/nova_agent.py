"""
NovaSync — Nova 2 Lite agentic planning loop.

Uses Amazon Bedrock Converse API with tool use to autonomously research
and generate trip scaffolds. The agent decides what to search for,
validates places, checks weather/events, and submits the final plan
via finalize_plan().
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time as _time
from datetime import date
from typing import Any
from uuid import uuid4 as _uuid4

from config import bedrock_runtime

logger = logging.getLogger(__name__)

MODEL_ID = "us.amazon.nova-lite-v1:0"  # cross-region inference prefix
MAX_ITERATIONS = 20

# Tools whose results are cached for deduplication.  Defined here (not inside
# the hot loop) so the set literal is created once at import time.
_DEDUP_TOOLS: frozenset[str] = frozenset(
    {"search_activities", "get_local_events", "get_weather", "validate_place"}
)

AGENT_SYSTEM_PROMPT = """
You are Nova, a senior travel agent at a boutique travel agency. You are meticulous, specific, and document your thinking carefully.

You receive the user's trip request plus initial evidence (from their provided links and uploaded photos). Your job is to RESEARCH and PLAN — use your tools to gather specific, verified information, then write the final scaffold plan.

═══════════════════════════════════════════════════════════
WORKING STYLE — Follow this exactly, every time:
═══════════════════════════════════════════════════════════

STEP 1 — PLAN FIRST (MANDATORY before ANY other tool call):
Call write_to_scratchpad with a numbered research checklist derived from the trip context.
This is your external memory — you MUST complete every item before calling finalize_plan.

Format your checklist as:
RESEARCH CHECKLIST for [City] ([start_date] – [end_date]):
[ ] 1. Search for [specific item from must_include] – find named venue
[ ] 2. Search for [second must_include item] – find named venue
[ ] 3. Get local events [start_date] to [end_date]
[ ] 4. Check weather for [city] [dates] – determines indoor/outdoor split
[ ] 5. Search best [cuisine/dietary requirement] in [city] – for dietary constraints
[ ] 6. Validate top venues: confirm they exist and are open
[ ] 7. Final review: all must_include covered, all days filled, no conflicts

STEP 2 — EXECUTE each checklist item with tools (see RESEARCH DEPTH below)

STEP 3 — TRACK PROGRESS (CRITICAL - NO DUPLICATES):
When completing tasks, update EXISTING lines from [ ] to [x] — DO NOT write new lines.
Keep the same task number, just change [ ] to [x] and add results.

CORRECT:
  Initial: "[ ] 1. Search for Uji matcha"
  Later:   "[x] 1. Search for Uji matcha – found: Hotel Tou, Granvia, Nohga"

WRONG (causes duplicates):
  "[ ] 1. Search for Uji matcha"
  "[x] 1. Search for Uji matcha – found: Hotel Tou..."  ← NEW line = duplicate

- Note retries: if a search fails, try an alternative query and log it.
- Write as if explaining your reasoning to a colleague.

STEP 4 — VERIFY BEFORE FINALIZING:
Before finalize_plan, write_to_scratchpad a final pass — scan your checklist,
confirm every [ ] item is resolved. You MAY NOT call finalize_plan if any checklist
item is still [ ] unchecked. If unverifiable, mark "UNVERIFIED – best available option" and include it.

STEP 5 — SELF-CRITIQUE BEFORE FINALIZING (MANDATORY):
Before calling finalize_plan, you MUST call self_critique_plan with your draft scaffold.
The critique will analyze:
- Are there empty or under-filled days?
- Does the plan match user's must_include items?
- Is there enough variety across days?
- Are weather considerations applied?
- DIRECTIVE ALIGNMENT: Compare plan against user directives (travel_party, dietary, fitness_level, accommodation_style, hard_constraints, soft_preferences, avoid, pace, budget_level). Score how well the plan satisfies each. Flag misalignments.

If the critique returns "needs_improvement":
1. Review the specific feedback
2. Call additional tools to fill gaps (search_activities, get_local_events, etc.)
3. Update your checklist with new research tasks
4. Complete the new research
5. Call self_critique_plan again

Only proceed to finalize_plan when critique returns "approved".

Do not take too many iterations, keep it to around 20 iterations.

STEP 6 — SELF-CORRECT: If validate_place returns not-found, search for an alternative and note the correction in scratchpad.

═══════════════════════════════════════════════════════════
RESEARCH DEPTH — Specificity rules (apply to ANY destination):
═══════════════════════════════════════════════════════════

SPECIFICITY RULE: When the user mentions any specific item — a food, neighborhood, experience, or attraction — extract that item as a search target and find the ACTUAL NAMED VENUES that offer it.

Pattern: user mentions X → search "best [X] [city] [year]" → find actual restaurant/shop/venue names. NEVER search just "[city] food" or "[city] activities" as generic queries. Always drill down to specific named places.

Keep queries short and natural — 4–8 words. Do NOT include "specific", "named", or "highly rated" as literal words in the query string.

Examples of the pattern (generalized):
- User mentions "ramen" → search "best ramen restaurants [city] 2026"
- User mentions "old town neighborhood" → search "[old town name] [city] top things to do"
- User mentions "temple near the market" → search "temples near [market name] [city] walking distance"
- User mentions "street food" → search "best street food [city] local favourites"
- User mentions a specific attraction → validate_place to confirm it exists before including

MINIMUM DEPTH:
- For each item in must_include: dedicate one search_activities call to finding the SPECIFIC VENUE or best version.
  This item MUST appear as a checklist entry before your first tool call.
- Minimum 3–4 distinct search_activities calls per trip (activities + food + neighborhood + specific items).
- Only use validate_place for verifying a NAMED PLACE (e.g. 'teamLab Planets Tokyo'). Never for concepts.
- Weather check: ALWAYS do this — it directly affects which activities go on which day.

VERIFICATION GATE: You MAY NOT call finalize_plan if any checklist item is still [ ] unchecked.
If a search returned nothing useful, try an alternative query, then mark [x] with what you found or
note "UNVERIFIED – included best available option".

═══════════════════════════════════════════════════════════
ask_user — Ask 1-3 questions to understand preferences:
═══════════════════════════════════════════════════════════

Ask the user when you encounter genuine ambiguity that would significantly affect their experience:

HIGH-IMPACT FORKS (always ask):
- Pace preference (relaxed 2–3 activities/day vs packed 5+/day)
- Two neighborhoods that are far apart and would determine the entire hotel/routing strategy
- Incompatible dietary constraints where combining would be impossible

PREFERENCE CLARIFICATIONS (ask if unclear):
- Budget level when not specified (backpacker vs mid-range vs luxury)
- Activity intensity preference when fitness level is unclear
- Indoor vs outdoor preference when weather is mixed
- Food preferences when dietary constraints are unclear

IMPORTANT: Aim to ask 1-3 questions to better understand the user. Engagement leads to better plans!

═══════════════════════════════════════════════════════════
PLANNING PHASE:
═══════════════════════════════════════════════════════════

PLAN QUALITY RULES:
1. Avoid repeating the same venue on different days (unless user explicitly requests multi-day events like conventions)
2. Each day should have a VARIED mix of activities (different from other days)
3. Use weather data: plan indoor activities on rainy/overcast days, outdoor on clear days
4. Include weather info for each day in the final plan output
5. Include specific venue names, not generic categories
6. Balance the itinerary: don't front-load all activities

FINALIZE_FORMAT:
- One line per day: "Day N (Weekday, Date) [Weather: X°C, Condition]: Morning activity · Afternoon activity · Evening activity"
- Include weather for each day in the format shown above
- Use specific named venues (e.g., "Kinkaku-ji Temple" not "visit temple")
- Vary activities across days for variety

When you have completed your research checklist and self-critique:
- Call finalize_plan(scaffold_text) with your complete scaffold
- Only include activities you have verified or have strong evidence for
- Do NOT write the scaffold as plain text — ALWAYS submit it through finalize_plan

═══════════════════════════════════════════════════════════
CRITICAL — HOW TO USE self_critique_plan AND finalize_plan:
═══════════════════════════════════════════════════════════

BEFORE calling self_critique_plan or finalize_plan, compose the FULL itinerary text in your head.
Then write ALL day lines verbatim as the tool input value.

WRONG (will be rejected):
  finalize_plan(scaffold_text="Final itinerary scaffold to be submitted.")
  self_critique_plan(draft_scaffold="Updated draft itinerary scaffold to be critiqued.")

CORRECT:
  finalize_plan(scaffold_text="Day 1 (Mon, Mar 10) [Weather: 12°C, Cloudy]: Kinkaku-ji Temple · Nishiki Market · Pontocho dining
Day 2 (Tue, Mar 11) [Weather: 15°C, Sunny]: Arashiyama Bamboo Grove · Tenryu-ji · Matcha café
Day 3 (Wed, Mar 12) [Weather: 14°C, Partly Cloudy]: Fushimi Inari Taisha · Tofuku-ji · Gion stroll")

The scaffold_text value IS the plan — every Day N line must appear verbatim inside the quotes.
"""

# Tool specs in Bedrock toolSpec format
AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "search_activities",
            "description": "Search the web for activities, restaurants, attractions, and things to do at a destination. Use specific queries like 'outdoor hiking family-friendly Tokyo' or 'best ramen Tokyo'. You decide the search query.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The destination city or region (e.g. 'Tokyo, Japan')",
                        },
                        "query": {
                            "type": "string",
                            "description": "The specific search query string (e.g. 'outdoor activities family friendly'). You construct this based on what you need to find.",
                        },
                    },
                    "required": ["location", "query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_local_events",
            "description": "Find real local events happening at the destination during the trip dates (concerts, festivals, sports, exhibitions). Use this to discover time-specific activities.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name (e.g. 'Tokyo')",
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY-MM-DD format",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in YYYY-MM-DD format",
                        },
                        "category": {
                            "type": "string",
                            "description": "Optional event category filter (e.g. 'music', 'sports', 'arts')",
                        },
                    },
                    "required": ["location", "start_date", "end_date"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_weather",
            "description": "Get weather forecast for the destination during trip dates. Use this to plan indoor/outdoor activities appropriately — swap outdoor hikes for indoor museums on rainy days.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "City name (e.g. 'Tokyo')",
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY-MM-DD format",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in YYYY-MM-DD format",
                        },
                    },
                    "required": ["city", "start_date", "end_date"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "validate_place",
            "description": "Validate that a specific place exists at a location and get its coordinates. Use before including lesser-known venues in the plan.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "place_name": {
                            "type": "string",
                            "description": "Name of the place to validate (e.g. 'teamLab Planets Tokyo')",
                        },
                        "location": {
                            "type": "string",
                            "description": "City or region context (e.g. 'Tokyo, Japan')",
                        },
                    },
                    "required": ["place_name", "location"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "write_to_scratchpad",
            "description": "Jot down planning observations and decisions that will be shown to the user. Examples: 'Day 3 is rainy — swapping outdoor hike for teamLab Planets', 'Sumo tournament found on Day 4 — added', 'User prefers relaxed pace — keeping 3 activities/day max'.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "string",
                            "description": "The planning observation or decision to record",
                        },
                    },
                    "required": ["note"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "ask_user",
            "description": "Pause planning and ask the user ONE clarifying question. Ask 1-3 questions total to understand preferences better. Good questions: pace, budget, activity intensity, neighborhood style, food preferences. Engagement leads to better plans!",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question to show the user — specific and concise.",
                        },
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {
                                        "type": "string",
                                        "description": "Button label shown to user",
                                    },
                                },
                                "required": ["id", "label"],
                            },
                            "minItems": 2,
                            "maxItems": 4,
                            "description": "2–4 answer options. User may also type a custom response.",
                        },
                    },
                    "required": ["question", "options"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "self_critique_plan",
            "description": (
                "Analyze your draft plan for quality issues before finalizing. "
                "IMPORTANT: You MUST pass the COMPLETE verbatim day-by-day text as draft_scaffold — "
                "NOT a description of it. Every 'Day N' line must be present in the value. "
                "Example of a correct call: "
                'draft_scaffold="Day 1 (Mon, Mar 10) [Weather: 12°C, Cloudy]: '
                "Kinkaku-ji Temple · Nishiki Market · Pontocho dining\\n"
                'Day 2 (Tue, Mar 11) [Weather: 15°C, Sunny]: Arashiyama bamboo · Tenryu-ji · Matcha café"'
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "draft_scaffold": {
                            "type": "string",
                            "description": (
                                "The FULL verbatim itinerary text — every Day N line. "
                                "Must start with 'Day 1 ...' and include all days. "
                                "Do NOT write a description or placeholder like 'Updated draft scaffold'. "
                                "Write the actual Day 1, Day 2, ... lines."
                            ),
                        },
                    },
                    "required": ["draft_scaffold"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "finalize_plan",
            "description": (
                "Submit the final itinerary. "
                "CRITICAL: scaffold_text MUST contain the FULL VERBATIM text of every day. "
                "Write all Day N lines directly as the value — do NOT write a description or placeholder. "
                "WRONG: scaffold_text='Final itinerary scaffold to be submitted.' "
                "CORRECT: scaffold_text='Day 1 (Mon, Mar 10) [Weather: 12°C, Cloudy]: "
                "Kinkaku-ji · Nishiki Market\\nDay 2 ...'"
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "scaffold_text": {
                            "type": "string",
                            "description": (
                                "The COMPLETE multi-day itinerary. "
                                "Format per line: 'Day N (Weekday, Date) [Weather: X°C, Condition]: "
                                "Activity · Activity · Activity'. "
                                "One line per day. All days must be present. "
                                "Write the actual content — never a description of it."
                            ),
                        },
                    },
                    "required": ["scaffold_text"],
                }
            },
        }
    },
]


_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    """Strip <thinking>...</thinking> blocks emitted by Nova Lite's CoT."""
    return _THINKING_RE.sub("", text).strip()


# ── Public API ─────────────────────────────────────────────────────────────


def run_nova_agent(
    *,
    scaffold_prompt: str,
    trip_location: str | None,
    start_date: date | None,
    end_date: date | None,
    action_queue: queue.Queue | None = None,
    cancel_event: threading.Event | None = None,
    question_event: threading.Event | None = None,
    question_answer: list[str | None] | None = None,
) -> tuple[str | None, list[dict], str, dict]:
    """
    Run the Nova 2 Lite agentic planning loop.

    Returns: (scaffold_text, agent_actions, scratchpad, debug_info)
    - scaffold_text: the final plan text (or None if failed/cancelled/timed out)
    - agent_actions: list of action dicts for the frontend feed
    - scratchpad: the agent's planning notes
    - debug_info: dict with warnings/errors
    """
    agent_actions: list[dict] = []
    scratchpad_parts: list[str] = []
    debug_info: dict[str, Any] = {"iterations": 0, "total_input_tokens": 0, "total_output_tokens": 0}

    # ── Deduplication state ───────────────────────────────────────────────────
    # Maps a canonical call key → cached result string.
    # When the agent repeats the exact same tool call, we return the cache and
    # force finalize_plan on the next iteration to break the loop.
    seen_tool_calls: dict[str, str] = {}
    consecutive_duplicate_count = 0
    force_finalize_next = False  # set to True after 2+ consecutive duplicates

    messages: list[dict] = [
        {"role": "user", "content": [{"text": scaffold_prompt}]}
    ]

    logger.info(
        "[nova_agent] Starting — model=%s max_iterations=%d prompt_chars=%d",
        MODEL_ID, MAX_ITERATIONS, len(scaffold_prompt),
    )

    for iteration in range(MAX_ITERATIONS):
        # ── Cancellation check ────────────────────────────────────────────────
        if cancel_event is not None and cancel_event.is_set():
            logger.info("[nova_agent] Cancelled by user at start of iteration %d", iteration + 1)
            debug_info["cancelled"] = True
            break

        logger.info(
            "[nova_agent] iteration=%d/%d history_messages=%d",
            iteration + 1, MAX_ITERATIONS, len(messages),
        )
        iter_started = _time.perf_counter()

        # Force write_to_scratchpad on iteration 0 — API-level guarantee.
        # Force finalize_plan if the agent has been looping on duplicate searches.
        if force_finalize_next:
            tool_choice = {"tool": {"name": "finalize_plan"}}
            force_finalize_next = False
            logger.warning(
                "[nova_agent] Forcing finalize_plan — agent was stuck on repeated searches"
            )
        elif iteration == 0:
            tool_choice = {"tool": {"name": "write_to_scratchpad"}}
        else:
            tool_choice = {"auto": {}}
        try:
            response = bedrock_runtime.converse(
                modelId=MODEL_ID,
                system=[{"text": AGENT_SYSTEM_PROMPT}],
                messages=messages,
                toolConfig={
                    "tools": AGENT_TOOLS,
                    "toolChoice": tool_choice,
                },
                inferenceConfig={"maxTokens": 4096, "temperature": 0.3},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[nova_agent] Bedrock converse failed on iteration %d: %s", iteration + 1, exc)
            debug_info["error"] = str(exc)
            break

        iter_elapsed_ms = (_time.perf_counter() - iter_started) * 1000
        stop_reason = response.get("stopReason", "")
        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)
        debug_info["iterations"] = iteration + 1
        debug_info["total_input_tokens"] = debug_info.get("total_input_tokens", 0) + input_tokens
        debug_info["total_output_tokens"] = debug_info.get("total_output_tokens", 0) + output_tokens

        logger.info(
            "[nova_agent] iteration=%d stop_reason=%s elapsed_ms=%.0f "
            "input_tokens=%d output_tokens=%d cumulative_tokens=%d",
            iteration + 1, stop_reason, iter_elapsed_ms,
            input_tokens, output_tokens,
            debug_info["total_input_tokens"] + debug_info["total_output_tokens"],
        )

        assistant_message = response["output"]["message"]
        messages.append(assistant_message)

        if stop_reason == "tool_use":
            tool_results: list[dict] = []

            pending_reasoning: list[str] = []

            for block in assistant_message["content"]:
                if "text" in block:
                    text = _strip_thinking(block["text"])
                    if text:
                        pending_reasoning.append(text)
                    continue
                if "toolUse" not in block:
                    continue

                tool_use = block["toolUse"]
                tool_name = tool_use["name"]
                tool_input = tool_use.get("input", {})
                tool_use_id = tool_use["toolUseId"]

                logger.info(
                    "[nova_agent] tool_call tool=%s input=%s",
                    tool_name,
                    json.dumps(tool_input, default=str)[:300],
                )

                # finalize_plan — return immediately (after validating content)
                if tool_name == "finalize_plan":
                    scaffold_text = tool_input.get("scaffold_text", "")
                    has_day_lines = bool(re.search(r"^Day\s+\d+", scaffold_text, re.MULTILINE))
                    logger.info(
                        "[nova_agent] finalize_plan called — scaffold_chars=%d has_day_lines=%s after %d iterations",
                        len(scaffold_text), has_day_lines, iteration + 1,
                    )
                    # Reject placeholder scaffolds — send error back so agent retries with real content
                    if not has_day_lines:
                        logger.warning(
                            "[nova_agent] finalize_plan rejected — no Day N lines in scaffold (chars=%d). "
                            "Returning error to agent.",
                            len(scaffold_text),
                        )
                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"text": (
                                    "ERROR: scaffold_text does not contain any 'Day N' lines — you passed a placeholder. "
                                    "DO NOT do more research. Your complete Day 1...Day N plan already exists in this "
                                    "conversation: look at the draft_scaffold value you passed to self_critique_plan just "
                                    "before this call. Copy that EXACT text verbatim as scaffold_text and call "
                                    "finalize_plan again immediately. Do not paraphrase, summarise, or start new searches."
                                )}],
                            }
                        })
                        # Record the failed attempt in the feed so the user can see it
                        agent_actions.append({
                            "tool_name": tool_name,
                            "summary": "⚠ finalize_plan rejected — placeholder text, retrying",
                            "tool_input": tool_input,
                            "result_preview": "scaffold_text had no Day N lines — agent must retry with full content",
                            "iteration": iteration + 1,
                            "reasoning": " ".join(pending_reasoning),
                        })
                        pending_reasoning = []
                        continue  # don't return — let the loop continue
                    action = {
                        "tool_name": tool_name,
                        "summary": _summarize_action(tool_name, tool_input),
                        "tool_input": tool_input,
                        "result_preview": f"Plan submitted ({len(scaffold_text)} chars)",
                        "iteration": iteration + 1,
                        "reasoning": " ".join(pending_reasoning),
                    }
                    pending_reasoning = []
                    agent_actions.append(action)
                    if action_queue is not None:
                        action_queue.put(action)
                    scratchpad = "\n".join(scratchpad_parts)
                    return scaffold_text, agent_actions, scratchpad, debug_info

                # ask_user — pause planning, emit question event, wait for answer
                if tool_name == "ask_user":
                    consecutive_duplicate_count = 0  # user interaction resets the dedup counter
                    question_id = str(_uuid4())
                    question_text = tool_input.get("question", "")
                    options = tool_input.get("options", [])

                    q_item = {
                        "type": "question",
                        "tool_name": "ask_user",
                        "question_id": question_id,
                        "question": question_text,
                        "options": options,
                        "summary": f"? {question_text[:70]}",
                        "iteration": iteration + 1,
                        "scratchpad": "\n".join(scratchpad_parts),
                    }
                    if action_queue is not None:
                        action_queue.put(q_item)

                    answer_text = "No answer provided — proceed with best judgment"
                    if question_event is not None and question_answer is not None:
                        answered = question_event.wait(timeout=120)
                        question_event.clear()
                        if cancel_event is not None and cancel_event.is_set():
                            debug_info["cancelled"] = True
                            break
                        if answered and question_answer[0]:
                            answer_text = question_answer[0]

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"User answered: {answer_text}"}],
                        }
                    })

                    answered_action = {
                        "tool_name": "ask_user",
                        "summary": f"? {question_text[:60]} -> {answer_text[:40]}",
                        "result_preview": f"User answered: {answer_text}",
                        "iteration": iteration + 1,
                        "scratchpad": "\n".join(scratchpad_parts),
                    }
                    agent_actions.append(answered_action)
                    if action_queue is not None:
                        action_queue.put(answered_action)
                    continue  # skip _execute_tool

                # write_to_scratchpad — record the note.
                # Also reset the duplicate counter: a scratchpad write between
                # two identical searches should not count toward the threshold.
                if tool_name == "write_to_scratchpad":
                    consecutive_duplicate_count = 0
                    note = tool_input.get("note", "")
                    scratchpad_parts.append(note)
                    logger.info("[nova_agent] scratchpad note: %s", note[:200])

                # ── Duplicate-call detection for research tools ────────────────
                # Reset counter when a non-research tool runs (write_to_scratchpad
                # etc. take early paths so we reset here for research tools only).
                call_key = f"{tool_name}::{json.dumps(tool_input, sort_keys=True)}"
                is_duplicate = tool_name in _DEDUP_TOOLS and call_key in seen_tool_calls

                if is_duplicate:
                    consecutive_duplicate_count += 1
                    cached = seen_tool_calls[call_key]
                    result_text = (
                        f"[DUPLICATE SEARCH — you already ran this exact query. "
                        f"Previous result: {cached[:300]}]\n\n"
                        "STOP repeating this search. Your research is complete enough. "
                        "Mark all remaining [ ] checklist items as [x] via write_to_scratchpad, "
                        "then call finalize_plan immediately with your best available Day 1…Day N scaffold."
                    )
                    logger.warning(
                        "[nova_agent] Duplicate tool call #%d: %s — returning cached result",
                        consecutive_duplicate_count, call_key[:120],
                    )
                    if consecutive_duplicate_count >= 2:
                        force_finalize_next = True
                        logger.warning(
                            "[nova_agent] %d consecutive duplicates — will force finalize_plan next iteration",
                            consecutive_duplicate_count,
                        )
                    tool_elapsed_ms = 0.0
                else:
                    consecutive_duplicate_count = 0
                    # Execute the tool with timing
                    tool_started = _time.perf_counter()
                    result_text = _execute_tool(tool_name, tool_input)
                    tool_elapsed_ms = (_time.perf_counter() - tool_started) * 1000
                    # Cache for dedup
                    if tool_name in _DEDUP_TOOLS:
                        seen_tool_calls[call_key] = result_text
                    # Append checklist-update reminder to every research tool result
                    if tool_name in _DEDUP_TOOLS:
                        result_text = (
                            result_text
                            + "\n\n[ACTION REQUIRED: Call write_to_scratchpad NOW to update the "
                            "corresponding [ ] checklist item to [x] with what you found. "
                            "Do not proceed to the next search without doing this first.]"
                        )

                logger.info(
                    "[nova_agent] tool_result tool=%s elapsed_ms=%.0f result_chars=%d preview=%s",
                    tool_name, tool_elapsed_ms, len(result_text),
                    result_text[:200].replace("\n", " "),
                )

                # Truncate search results to 600 chars for the conversation history,
                # but always preserve the full ACTION REQUIRED reminder if present.
                if "[ACTION REQUIRED:" in result_text:
                    main_part, _, reminder = result_text.partition("\n\n[ACTION REQUIRED:")
                    result_text_truncated = main_part[:500] + "\n\n[ACTION REQUIRED:" + reminder
                elif "[DUPLICATE SEARCH" in result_text:
                    result_text_truncated = result_text  # always send full dedup message
                else:
                    result_text_truncated = result_text[:600]

                # Build rich action for the UI feed
                action = {
                    "tool_name": tool_name,
                    "summary": _summarize_action(tool_name, tool_input),
                    "tool_input": tool_input,
                    "result_preview": result_text[:400],
                    "elapsed_ms": round(tool_elapsed_ms),
                    "iteration": iteration + 1,
                    "reasoning": " ".join(pending_reasoning),
                    "scratchpad": "\n".join(scratchpad_parts),
                }
                pending_reasoning = []
                agent_actions.append(action)
                if action_queue is not None:
                    action_queue.put(action)

                # Build tool result for the conversation
                tool_results.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": result_text_truncated}],
                    }
                })

            # Append tool results as the next user message (guard against empty list on cancellation)
            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        elif stop_reason == "end_turn":
            # _extract_text_content already strips thinking tags internally
            stripped_text = _extract_text_content(response) or ""

            # Only use as scaffold fallback if the text actually contains Day N lines
            if stripped_text and re.search(r"^Day\s+\d+", stripped_text, re.MULTILINE):
                logger.warning(
                    "[nova_agent] end_turn fallback — scaffold-like text detected, using as plan. "
                    "text_chars=%d", len(stripped_text),
                )
                debug_info["warning"] = "used_end_turn_fallback"
                scratchpad = "\n".join(scratchpad_parts)
                return stripped_text, agent_actions, scratchpad, debug_info

            # The agent wrote a planning note / checklist as prose — inject a reminder and continue
            logger.warning(
                "[nova_agent] end_turn without scaffold (chars=%d, iteration=%d) — "
                "injecting tool reminder and continuing",
                len(stripped_text), iteration + 1,
            )
            messages.append({
                "role": "user",
                "content": [{
                    "text": (
                        "You wrote text instead of calling a tool. "
                        "Please continue by calling tools — use write_to_scratchpad for notes/checklists, "
                        "and submit your final plan ONLY via finalize_plan. "
                        "Do NOT write the plan or checklist as free text."
                    ),
                }],
            })

        else:
            logger.warning("[nova_agent] Unexpected stop reason: %s", stop_reason)
            debug_info["warning"] = f"unexpected_stop_reason: {stop_reason}"
            break

    # Loop ended — either cancelled, max iterations, or unexpected stop
    if debug_info.get("cancelled"):
        logger.info("[nova_agent] Returning after cancellation — %d actions collected", len(agent_actions))
        scratchpad = "\n".join(scratchpad_parts)
        return None, agent_actions, scratchpad, debug_info

    # Max iterations exhausted — attempt one emergency finalize_plan call
    logger.warning(
        "[nova_agent] Max iterations reached — attempting emergency finalize with current research"
    )
    debug_info["warning"] = "max_iterations_reached"
    emergency_msg = (
        "You have reached the maximum number of planning steps. "
        "Based on all the research you have done so far, write the best possible "
        "complete Day-by-Day itinerary now. "
        "Format: 'Day N (Weekday, Date) [Weather: X°C, Condition]: Activity · Activity · Activity'. "
        "Call finalize_plan with the FULL text immediately."
    )
    messages.append({"role": "user", "content": [{"text": emergency_msg}]})
    try:
        emergency_resp = bedrock_runtime.converse(
            modelId=MODEL_ID,
            system=[{"text": AGENT_SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={
                "tools": AGENT_TOOLS,
                "toolChoice": {"tool": {"name": "finalize_plan"}},
            },
            inferenceConfig={"maxTokens": 4096, "temperature": 0.3},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[nova_agent] Emergency draft Bedrock call failed: %s", exc)
    else:
        # Parse outside the except so structural errors surface as real exceptions
        for block in emergency_resp.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in block and block["toolUse"]["name"] == "finalize_plan":
                scaffold_text = block["toolUse"]["input"].get("scaffold_text", "")
                if re.search(r"^Day\s+\d+", scaffold_text, re.MULTILINE):
                    logger.info(
                        "[nova_agent] Emergency draft produced scaffold_chars=%d", len(scaffold_text)
                    )
                    debug_info["warning"] = "used_emergency_draft"
                    scratchpad = "\n".join(scratchpad_parts)
                    return scaffold_text, agent_actions, scratchpad, debug_info
        logger.warning("[nova_agent] Emergency draft response contained no valid Day N scaffold")

    # Genuine failure
    scratchpad = "\n".join(scratchpad_parts)
    return None, agent_actions, scratchpad, debug_info


# ── Private helpers ────────────────────────────────────────────────────────


def _extract_activities_from_day_line(line: str) -> list[str]:
    """
    Extract activity strings from a scaffold day line.
    Handles [Weather: X°C] by finding the separator colon after the last ']'.
    """
    last_bracket = line.rfind("]")
    if last_bracket > -1:
        sep = line.find(":", last_bracket)
    else:
        sep = line.find(":")
    if sep == -1:
        return []
    activities_part = line[sep + 1:]
    return [a.strip().lower() for a in activities_part.split("·") if a.strip()]


def _execute_self_critique(draft_scaffold: str) -> str:
    """
    Analyze draft plan for quality issues.
    Returns JSON with verdict, feedback, and alignment scores.

    Structural issues (trigger needs_improvement):
      - Empty days (< 2 activities)
      - Missing weather bracket
      - Exact same full activity string repeated across days

    Advisory notes (do NOT trigger needs_improvement — logged as suggestions only):
      - One activity type dominates >70% of all slots
    """
    structural_issues = []
    suggestions = []

    # Parse days from scaffold
    day_lines = [line.strip() for line in draft_scaffold.split("\n") if line.strip().lower().startswith("day ")]

    # Guard: reject placeholder / empty scaffolds immediately
    if not day_lines:
        return json.dumps({
            "verdict": "needs_improvement",
            "feedback": (
                "ERROR: draft_scaffold does not contain any 'Day N' lines. "
                "You passed a placeholder description instead of the actual itinerary. "
                "Compose the full day-by-day plan text and pass it verbatim as draft_scaffold. "
                "Example: 'Day 1 (Mon, Mar 10) [Weather: 12°C, Cloudy]: Kinkaku-ji · Nishiki Market'"
            ),
            "alignment_score": {"overall": 0},
            "day_count": 0,
            "activity_count": 0,
            "structural_issues": 1,
            "suggestions": 0,
        })

    # Check 1: Empty or under-filled days
    for i, line in enumerate(day_lines):
        acts = _extract_activities_from_day_line(line)
        if len(acts) < 2:
            structural_issues.append(
                f"Day {i + 1} has very few activities ({len(acts)}). Add at least 2–3."
            )

    # Check 2: Exact duplicate activities (verbatim, across days)
    # Only flags when the COMPLETE activity string repeats — different venues are fine.
    all_activities: list[str] = []
    for line in day_lines:
        all_activities.extend(_extract_activities_from_day_line(line))

    # Activities containing "(placeholder)" are intentionally unfilled — skip duplicate check for them
    # and surface them as a separate advisory note instead.
    _placeholder_markers = ("(placeholder)", "[placeholder]", "[unverified", "placeholder)")
    placeholder_acts = [a for a in all_activities if any(m in a for m in _placeholder_markers)]
    checkable_acts = [a for a in all_activities if not any(m in a for m in _placeholder_markers)]

    seen: set[str] = set()
    exact_repeats: list[str] = []
    for act in checkable_acts:
        if act in seen and len(act) > 8:
            exact_repeats.append(act)
        seen.add(act)

    if exact_repeats:
        structural_issues.append(
            f"Same activity appears on multiple days: {', '.join(set(exact_repeats[:3]))}. "
            "Use different venues or experiences each day."
        )

    if placeholder_acts:
        unique_placeholders = len(set(placeholder_acts))
        suggestions.append(
            f"{unique_placeholders} activity slot(s) still use '(placeholder)'. "
            "Try a different search query to find a named venue. "
            "If still unresolved after 2 attempts, write it as "
            "'Best available [type] restaurant in [city] [UNVERIFIED]' — "
            "never repeat the same placeholder text across days."
        )

    # Check 3: Weather inclusion (structural — the format requires it)
    has_weather = any("°c" in line.lower() or "[weather" in line.lower() for line in day_lines)
    if not has_weather:
        structural_issues.append(
            "Weather bracket missing from day headers. "
            "Add [Weather: X°C, Condition] to each day line."
        )

    # Advisory: activity-type dominance (>70% threshold, does NOT block approval)
    type_keywords = {"food/dining": ["ramen", "sushi", "restaurant", "café", "cafe", "izakaya", "kaiseki", "matcha", "cuisine"],
                     "temples/shrines": ["temple", "shrine", "taisha", "ji ", "-ji", "torii"],
                     "museums": ["museum", "gallery", "exhibition"]}
    type_counts: dict[str, int] = {k: 0 for k in type_keywords}
    for act in all_activities:
        for category, keywords in type_keywords.items():
            if any(kw in act for kw in keywords):
                type_counts[category] += 1
                break

    if all_activities:
        dominant = max(type_counts, key=type_counts.get)
        dominant_pct = type_counts[dominant] / len(all_activities)
        if dominant_pct > 0.70 and type_counts[dominant] >= 3:
            suggestions.append(
                f"{dominant.capitalize()} activities make up {int(dominant_pct * 100)}% of the plan. "
                "Consider adding a contrasting experience (market, neighbourhood walk, viewpoint, etc.)."
            )

    # Calculate score: structural issues cost 20pts each; advisory suggestions cost 5pts
    score = 100 - len(structural_issues) * 20 - len(suggestions) * 5
    if len(day_lines) < 2:
        score -= 30

    # Verdict: only structural issues can block approval
    if structural_issues:
        verdict = "needs_improvement"
        feedback_parts = ["Structural issues that must be fixed:"] + [f"- {i}" for i in structural_issues]
        if suggestions:
            feedback_parts += ["", "Suggestions (optional):"] + [f"- {s}" for s in suggestions]
        feedback = "\n".join(feedback_parts)
    else:
        verdict = "approved"
        finalize_reminder = (
            "\n\nNEXT STEP: Call finalize_plan NOW with scaffold_text = the EXACT same "
            "Day 1...Day N text you just passed as draft_scaffold in this self_critique_plan call. "
            "Copy it verbatim — do not paraphrase, do not start new searches."
        )
        if suggestions:
            feedback = "Plan approved. Optional suggestions:\n" + "\n".join(f"- {s}" for s in suggestions) + finalize_reminder
        else:
            feedback = "Plan approved. Strong variety, weather data included, no duplicate venues." + finalize_reminder

    result = {
        "verdict": verdict,
        "feedback": feedback,
        "alignment_score": {"overall": max(0, min(100, score))},
        "day_count": len(day_lines),
        "activity_count": len(all_activities),
        "structural_issues": len(structural_issues),
        "suggestions": len(suggestions),
    }

    return json.dumps(result, indent=2)


def _execute_tool(tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "search_activities":
            from services.tools.search_tool import search_activities
            return search_activities(tool_input["location"], tool_input["query"])
        elif tool_name == "get_local_events":
            from services.tools.events_tool import get_local_events
            return get_local_events(
                tool_input["location"],
                tool_input["start_date"],
                tool_input.get("end_date", tool_input["start_date"]),
                tool_input.get("category"),
            )
        elif tool_name == "get_weather":
            from services.tools.weather_tool import get_weather
            return get_weather(tool_input["city"], tool_input["start_date"], tool_input["end_date"])
        elif tool_name == "validate_place":
            from services.tools.places_tool import validate_place
            return validate_place(tool_input["place_name"], tool_input["location"])
        elif tool_name == "write_to_scratchpad":
            return f"Note recorded: {tool_input.get('note', '')}"
        elif tool_name == "self_critique_plan":
            return _execute_self_critique(tool_input.get("draft_scaffold", ""))
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tool %s failed: %s", tool_name, exc)
        return f"Tool error: {exc}"


def _summarize_action(tool_name: str, tool_input: dict) -> str:
    if tool_name == "search_activities":
        query = tool_input.get("query", "")
        location = tool_input.get("location", "")
        display = query if location.lower() in query.lower() else f"{query} {location}".strip()
        return f"Searching \"{display}\""
    elif tool_name == "get_local_events":
        return f"Finding events in {tool_input.get('location', '')} ({tool_input.get('start_date', '')} – {tool_input.get('end_date', '')})"
    elif tool_name == "get_weather":
        return f"Checking weather in {tool_input.get('city', '')} ({tool_input.get('start_date', '')} – {tool_input.get('end_date', '')})"
    elif tool_name == "validate_place":
        return f"Validating \"{tool_input.get('place_name', '')}\" in {tool_input.get('location', '')}"
    elif tool_name == "write_to_scratchpad":
        return f"Note: {tool_input.get('note', '')[:80]}"
    elif tool_name == "self_critique_plan":
        return "Self-critiquing draft plan for quality issues"
    elif tool_name == "finalize_plan":
        return "Submitting final plan"
    return tool_name


def _extract_text_content(response: dict) -> str | None:
    for block in response.get("output", {}).get("message", {}).get("content", []):
        if "text" in block:
            stripped = _strip_thinking(block["text"]).strip()
            if stripped:
                return stripped
    return None


async def revise_scaffold_with_nova(
    original_scaffold: str,
    user_feedback: str,
    original_idea: str,
) -> str:
    """
    Revise a trip scaffold using Nova 2 Lite on Bedrock.
    Single-turn, no tool calls. Returns revised scaffold or original on failure.
    """
    import asyncio

    system_prompt = (
        "You are a travel itinerary planner. "
        "The user has a draft trip scaffold and wants to revise it. "
        "Return ONLY the revised scaffold, keeping the exact same format: "
        "'Day N (Weekday, Date) [Weather: X°C, Condition]: Activity · Activity · Activity'. "
        "One line per day. Do not add any explanation or preamble."
    )

    user_message = (
        f"Original trip idea:\n{original_idea}\n\n"
        f"Current scaffold:\n{original_scaffold}\n\n"
        f"User feedback:\n{user_feedback}\n\n"
        "Please revise the scaffold to incorporate this feedback."
    )

    def _call_bedrock() -> str:
        response = bedrock_runtime.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 2048, "temperature": 0.3},
        )
        return _extract_text_content(response) or original_scaffold

    try:
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, _call_bedrock),
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[nova_agent] revise_scaffold_with_nova failed: %s", exc)
        return original_scaffold
