// Maps a LifestyleProfile (from the onboarding modal) into InputDirectives fields.
// Pure function — no side effects.

import type { InputDirectives, LifestyleProfile } from "../types";

export function lifestyleToDirectives(
    profile: LifestyleProfile,
    base: InputDirectives,
): InputDirectives {
    const soft: string[] = [...(base.soft_preferences ?? [])];
    const hard: string[] = [...(base.hard_constraints ?? [])];
    let mobilityMode = base.mobility_mode ?? null;
    let pace = base.pace ?? null;
    let mobilityModeFromFitness: string | null = null;

    // Travel party
    for (const party of profile.travel_party) {
        if (party === "family_young_kids") {
            soft.push("kid-friendly venues");
            soft.push("family-friendly dining options");
        } else if (party === "elderly") {
            soft.push("accessible routes suitable for elderly");
        } else if (party === "partner") {
            soft.push("couple-friendly experiences");
        }
    }

    // Wake time
    if (profile.wake_time_pref === "early_bird") {
        soft.push("day can start from 07:00");
    } else if (profile.wake_time_pref === "late_riser") {
        soft.push("day should start no earlier than 10:00");
    }

    // Dietary
    for (const diet of profile.dietary) {
        if (diet === "none") continue;
        soft.push(`${diet} dining options`);
        if (diet === "halal") {
            hard.push("only halal dining venues");
        } else if (diet === "kosher") {
            hard.push("only kosher dining venues");
        }
    }

    // Fitness level
    if (profile.fitness_level === "low") {
        mobilityModeFromFitness = "accessible";
        hard.push("avoid hikes longer than 1 hour");
        soft.push("gentle walking routes");
    } else if (profile.fitness_level === "high") {
        soft.push("active and adventurous activities welcome");
    }

    // Accommodation style
    if (profile.accommodation_style) {
        soft.push(`${profile.accommodation_style.replace("_", "-")} accommodation`);
    }

    // Pace (overrides existing if provided)
    if (profile.pace) {
        pace = profile.pace;
    }

    // Mobility mode: profile.mobility_mode wins, then fitness-derived, then base
    if (profile.mobility_mode && profile.mobility_mode !== "none") {
        mobilityMode = profile.mobility_mode;
    } else if (mobilityModeFromFitness && !mobilityMode) {
        mobilityMode = mobilityModeFromFitness;
    }

    // Deduplicate
    const dedup = (arr: string[]) => [...new Set(arr.filter(Boolean))];

    return {
        ...base,
        soft_preferences: dedup(soft),
        hard_constraints: dedup(hard),
        mobility_mode: mobilityMode,
        pace,
        travel_party: profile.travel_party,
        dietary: profile.dietary,
        wake_time_pref: profile.wake_time_pref,
        fitness_level: profile.fitness_level,
        accommodation_style: profile.accommodation_style,
    };
}
