import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";

/**
 * OAuth callback landing page — NOT wrapped in ProtectedRoute.
 * Supabase redirects here with #access_token in the hash.
 * We explicitly call getSession() to trigger hash exchange,
 * then navigate to the saved destination or /dashboard.
 */
export default function AuthCallbackPage() {
    const navigate = useNavigate();

    useEffect(() => {
        supabase.auth.getSession().then(({ data: { session } }) => {
            if (session) {
                const saved = sessionStorage.getItem("login_redirect");
                sessionStorage.removeItem("login_redirect");
                navigate(saved && saved !== "/login" ? saved : "/dashboard", { replace: true });
            } else {
                // Hash exchange failed or no tokens — back to login
                navigate("/login", { replace: true });
            }
        });
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
