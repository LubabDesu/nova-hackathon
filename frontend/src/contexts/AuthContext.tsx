import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Session, User } from "@supabase/supabase-js";
import { supabase } from "../lib/supabase";

interface AuthContextValue {
    user: User | null;
    session: Session | null;
    loading: boolean;
    signInWithGoogle: () => Promise<void>;
    signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [session, setSession] = useState<Session | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        // Detect if the current URL carries OAuth tokens (implicit flow hash or PKCE code).
        // If so, Supabase needs time to exchange them — don't flip loading=false on
        // the initial null-session event or ProtectedRoute will navigate away and
        // destroy the hash before the exchange completes.
        const isOAuthCallback =
            window.location.hash.includes("access_token") ||
            window.location.search.includes("code=");

        const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
            setSession(session);
            setUser(session?.user ?? null);

            if (isOAuthCallback && event === "INITIAL_SESSION" && !session) {
                // Hash/code not yet exchanged — stay in loading state
                return;
            }
            setLoading(false);
        });

        // Safety net: if OAuth exchange never completes, stop loading after 5 s
        let timeout: ReturnType<typeof setTimeout> | undefined;
        if (isOAuthCallback) {
            timeout = setTimeout(() => setLoading(false), 5000);
        }

        return () => {
            subscription.unsubscribe();
            if (timeout) clearTimeout(timeout);
        };
    }, []);

    const signInWithGoogle = async () => {
        await supabase.auth.signInWithOAuth({
            provider: "google",
            options: { redirectTo: `${window.location.origin}/dashboard` },
        });
    };

    const signOut = async () => {
        await supabase.auth.signOut();
    };

    return (
        <AuthContext.Provider value={{ user, session, loading, signInWithGoogle, signOut }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
    return ctx;
}
