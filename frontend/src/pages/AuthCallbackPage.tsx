import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";

/**
 * OAuth callback landing page — NOT wrapped in ProtectedRoute.
 *
 * Supabase redirects here with #access_token=...&refresh_token=... in the hash.
 * We manually parse the hash and call setSession() to establish the session,
 * then navigate to the saved destination or /dashboard.
 */
export default function AuthCallbackPage() {
    const navigate = useNavigate();

    useEffect(() => {
        async function handleOAuthCallback() {
            const redirectTo = () => {
                const saved = sessionStorage.getItem("login_redirect");
                sessionStorage.removeItem("login_redirect");
                navigate(saved && saved !== "/login" ? saved : "/dashboard", { replace: true });
            };

            // 1. Try parsing tokens from the URL hash (implicit flow)
            const hash = window.location.hash.substring(1);
            const params = new URLSearchParams(hash);
            const accessToken = params.get("access_token");
            const refreshToken = params.get("refresh_token");

            if (accessToken && refreshToken) {
                const { data, error } = await supabase.auth.setSession({
                    access_token: accessToken,
                    refresh_token: refreshToken,
                });
                if (data.session && !error) {
                    redirectTo();
                    return;
                }
                console.error("setSession failed:", error);
            }

            // 2. Fallback: maybe the client already processed the hash
            const { data: { session } } = await supabase.auth.getSession();
            if (session) {
                redirectTo();
                return;
            }

            // 3. Nothing worked — back to login
            navigate("/login", { replace: true });
        }

        handleOAuthCallback();
    }, [navigate]);

    return (
        <div style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "'DM Sans', sans-serif",
            color: "#64748b",
            background: "linear-gradient(175deg, #bdd9f4 0%, #f4f8fc 80%)",
        }}>
            Signing you in…
        </div>
    );
}
