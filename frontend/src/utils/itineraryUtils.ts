import type { ItineraryNode } from "../types";

/** Add HH:MM + minutes → HH:MM string */
function addMins(time: string, mins: number): string {
    const [h, m] = time.split(":").map(Number);
    const total = h * 60 + m + mins;
    const hh = String(Math.floor(total / 60) % 24).padStart(2, "0");
    const mm = String(total % 60).padStart(2, "0");
    return `${hh}:${mm}`;
}

/**
 * Given an ordered list of nodes for a single day, re-assigns start/end times
 * sequentially from the first node's current start time, preserving durations.
 * If the first node has no start time, defaults to "09:00".
 */
export function repackDay(nodes: ItineraryNode[]): ItineraryNode[] {
    if (nodes.length === 0) return [];
    let cursor = nodes[0].start_time_local ?? "09:00";
    return nodes.map((node) => {
        const duration = node.duration_mins ?? 60;
        const start = cursor;
        const end = addMins(start, duration);
        cursor = end;
        return { ...node, start_time_local: start, end_time_local: end };
    });
}

/** Returns warning strings if any day has activities ending past 23:00. */
export function validatePlan(
    dayGroups: Array<{ key: string; items: ItineraryNode[] }>,
): string[] {
    const warnings: string[] = [];
    for (const group of dayGroups) {
        const last = group.items[group.items.length - 1];
        if (last?.end_time_local && last.end_time_local > "23:00") {
            warnings.push(
                `Day ${group.key}: activities end at ${last.end_time_local}, past 23:00.`,
            );
        }
    }
    return warnings;
}

/** True if a node is an auto-generated filler (hidden in edit mode). */
export function isFillerNode(node: ItineraryNode): boolean {
    return node.segment_origin === "synthetic";
}
