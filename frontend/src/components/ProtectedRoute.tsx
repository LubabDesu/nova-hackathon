import { Navigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import type { ReactNode } from "react";

export default function ProtectedRoute({ children }: { children: ReactNode }) {
    const { user, loading } = useAuth();
    if (loading) return <div className="loading-screen">Loading...</div>;
    if (!user) {
        // Save intended destination so LoginPage can redirect back after OAuth
        sessionStorage.setItem("login_redirect", window.location.pathname + window.location.search);
        return <Navigate to="/login" replace />;
    }
    return <>{children}</>;
}
