// NovaSync — activity type → emoji + color mapping utility

export type ActivityType =
    | "food" | "dining" | "restaurant"
    | "culture" | "museum" | "art"
    | "outdoor" | "nature" | "hiking"
    | "transport" | "transfer"
    | "accommodation" | "hotel"
    | "entertainment" | "nightlife"
    | "shopping"
    | "wellness" | "spa"
    | string; // fallback

export interface ActivityStyle {
    emoji: string;
    color: string;      // CSS color for accent/border
    bgColor: string;    // CSS background color (translucent)
}

export function getActivityStyle(activityType: ActivityType): ActivityStyle {
    const type = (activityType || "").toLowerCase();

    if (type.includes("food") || type.includes("dining") || type.includes("restaurant") || type.includes("cafe")) {
        return { emoji: "\uD83C\uDF7D\uFE0F", color: "#F59E0B", bgColor: "rgba(245,158,11,0.15)" };
    }
    if (type.includes("museum") || type.includes("culture") || type.includes("art") || type.includes("history")) {
        return { emoji: "\uD83C\uDFDB\uFE0F", color: "#8B5CF6", bgColor: "rgba(139,92,246,0.15)" };
    }
    if (type.includes("outdoor") || type.includes("nature") || type.includes("hiking") || type.includes("park")) {
        return { emoji: "\uD83C\uDF3F", color: "#10B981", bgColor: "rgba(16,185,129,0.15)" };
    }
    if (type.includes("transport") || type.includes("transfer") || type.includes("flight") || type.includes("train")) {
        return { emoji: "\uD83D\uDE86", color: "#6B7280", bgColor: "rgba(107,114,128,0.15)" };
    }
    if (type.includes("hotel") || type.includes("accommodation") || type.includes("check")) {
        return { emoji: "\uD83C\uDFE8", color: "#3B82F6", bgColor: "rgba(59,130,246,0.15)" };
    }
    if (type.includes("entertainment") || type.includes("nightlife") || type.includes("show") || type.includes("concert")) {
        return { emoji: "\uD83C\uDFAD", color: "#EC4899", bgColor: "rgba(236,72,153,0.15)" };
    }
    if (type.includes("shopping") || type.includes("market")) {
        return { emoji: "\uD83D\uDECD\uFE0F", color: "#F97316", bgColor: "rgba(249,115,22,0.15)" };
    }
    if (type.includes("wellness") || type.includes("spa") || type.includes("relax")) {
        return { emoji: "\uD83E\uDDD8", color: "#14B8A6", bgColor: "rgba(20,184,166,0.15)" };
    }
    if (type.includes("beach") || type.includes("water") || type.includes("swim") || type.includes("snorkel")) {
        return { emoji: "\uD83C\uDFD6\uFE0F", color: "#0EA5E9", bgColor: "rgba(14,165,233,0.15)" };
    }
    // default
    return { emoji: "\uD83D\uDCCD", color: "#4A90D9", bgColor: "rgba(74,144,217,0.15)" };
}
