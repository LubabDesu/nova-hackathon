import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import "./LoginPage.css";

const GoogleIcon = () => (
    <svg viewBox="0 0 24 24" width="18" height="18" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
);

export default function LoginPage() {
    const { user, loading, signInWithGoogle } = useAuth();
    const navigate = useNavigate();

    useEffect(() => {
        if (!loading && user) {
            const savedPath = sessionStorage.getItem("login_redirect");
            sessionStorage.removeItem("login_redirect");
            navigate(savedPath && savedPath !== "/login" ? savedPath : "/dashboard", { replace: true });
        }
    }, [user, loading, navigate]);

    return (
        <div className="lp-root">
            {/* Sky background */}
            <div className="lp-sky" />

            {/* Decorative sphere arcs */}
            <div className="lp-arc lp-arc-1" />
            <div className="lp-arc lp-arc-2" />

            {/* Drifting clouds */}
            <div className="lp-cloud lp-cloud-1" />
            <div className="lp-cloud lp-cloud-2" />
            <div className="lp-cloud lp-cloud-3" />
            <div className="lp-cloud lp-cloud-4" />
            <div className="lp-cloud lp-cloud-5" />

            {/* Wordmark */}
            <div className="lp-wordmark" aria-label="NovaSync">
                <span className="lp-wordmark-star" aria-hidden="true">✦</span>
                NovaSync
            </div>

            {/* Card */}
            <div className="lp-card-wrap">
                <div className="lp-card">
                    <div className="lp-icon" aria-hidden="true">✈︎</div>

                    <h1 className="lp-title">
                        Plan your next<br />
                        <em>adventure</em>
                    </h1>

                    <p className="lp-subtitle">
                        AI-powered itineraries for groups.<br />
                        Sign in to start exploring.
                    </p>

                    <button
                        className="lp-google-btn"
                        onClick={signInWithGoogle}
                        disabled={loading}
                        aria-label="Continue with Google"
                    >
                        <GoogleIcon />
                        Continue with Google
                    </button>

                    <p className="lp-terms">
                        By continuing, you agree to our{" "}
                        <a href="#">Terms</a> and <a href="#">Privacy Policy</a>
                    </p>
                </div>
            </div>
        </div>
    );
}
