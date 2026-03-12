// NovaSync — BookingModal
// Full-screen modal overlay wrapping BookingPanel for restaurant reservations.

import { useEffect } from "react";
import BookingPanel from "./BookingPanel";

interface BookingModalProps {
    isOpen: boolean;
    onClose: () => void;
    restaurantName: string;
    city: string;
    date: string;
    time: string;
    partySize: number;
}

export default function BookingModal({
    isOpen,
    onClose,
    restaurantName,
    city,
    date,
    time,
    partySize,
}: BookingModalProps) {
    // Lock body scroll when modal is open
    useEffect(() => {
        if (isOpen) {
            document.body.style.overflow = "hidden";
        } else {
            document.body.style.overflow = "";
        }
        return () => {
            document.body.style.overflow = "";
        };
    }, [isOpen]);

    // Escape key intentionally disabled during booking to prevent accidental cancellation.
    // Users can close via the × button which goes through BookingPanel's cancel flow.

    if (!isOpen) return null;

    return (
        <div
            style={{
                position: "fixed",
                inset: 0,
                zIndex: 1000,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(11, 31, 56, 0.6)",
                backdropFilter: "blur(4px)",
                padding: "1rem",
            }}
            // onClick intentionally omitted — clicking outside does not close during booking
        >
            <div
                style={{
                    position: "relative",
                    width: "100%",
                    maxWidth: 560,
                    maxHeight: "90vh",
                    overflowY: "auto",
                    borderRadius: 16,
                    boxShadow: "0 24px 64px rgba(11,31,56,0.3)",
                }}
            >
                {/* Modal header */}
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "1rem 1.25rem 0.75rem",
                        background: "rgba(255,255,255,0.95)",
                        borderBottom: "1px solid rgba(74,141,196,0.15)",
                        borderRadius: "16px 16px 0 0",
                        position: "sticky",
                        top: 0,
                        zIndex: 1,
                    }}
                >
                    <div>
                        <p style={{ margin: 0, fontSize: "0.75rem", color: "#4a6a8a", fontFamily: "'DM Sans', sans-serif", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                            Nova Act Booking
                        </p>
                        <h2 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 600, color: "#0b1f38", fontFamily: "'DM Sans', sans-serif" }}>
                            {restaurantName}
                        </h2>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        style={{
                            background: "none",
                            border: "none",
                            cursor: "pointer",
                            color: "#4a6a8a",
                            fontSize: "1.25rem",
                            lineHeight: 1,
                            padding: "4px 8px",
                            borderRadius: 6,
                        }}
                        aria-label="Close booking modal"
                    >
                        ×
                    </button>
                </div>

                {/* Panel (no border-radius top since header handles it) */}
                <div style={{ borderRadius: "0 0 16px 16px", overflow: "hidden" }}>
                    <BookingPanel
                        restaurantName={restaurantName}
                        city={city}
                        date={date}
                        time={time}
                        partySize={partySize}
                        onClose={onClose}
                    />
                </div>
            </div>
        </div>
    );
}
