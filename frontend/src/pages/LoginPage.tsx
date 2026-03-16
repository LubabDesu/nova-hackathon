import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { GoogleLogin } from "@react-oauth/google";
import { useAuth } from "../contexts/AuthContext";
import "./LoginPage.css";

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

                    <div className="lp-google-wrap">
                        <GoogleLogin
                            onSuccess={(response) => {
                                if (response.credential) {
                                    signInWithGoogle(response.credential).catch((err) =>
                                        console.error("Supabase signInWithIdToken failed:", err)
                                    );
                                }
                            }}
                            onError={() => console.error("Google Sign-In popup failed")}
                            theme="outline"
                            size="large"
                            width="300"
                            text="continue_with"
                            shape="pill"
                        />
                    </div>

                    <p className="lp-terms">
                        By continuing, you agree to our{" "}
                        <a href="#">Terms</a> and <a href="#">Privacy Policy</a>
                    </p>
                </div>
            </div>
        </div>
    );
}
