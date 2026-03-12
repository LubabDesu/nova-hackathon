// NovaSync — Booking Status Badge component
// Simple badge showing booking status: pending, confirmed, or failed

interface BookingStatusBadgeProps {
    status: "pending" | "confirmed" | "failed";
}

const STATUS_CONFIG = {
    pending: {
        label: "Pending",
        icon: "⏳",
        className: "booking-badge--pending",
    },
    confirmed: {
        label: "Confirmed",
        icon: "✅",
        className: "booking-badge--confirmed",
    },
    failed: {
        label: "Failed",
        icon: "❌",
        className: "booking-badge--failed",
    },
};

export default function BookingStatusBadge({ status }: BookingStatusBadgeProps) {
    const config = STATUS_CONFIG[status];

    return (
        <span className={`booking-status-badge ${config.className}`}>
            <span className="booking-badge-icon">{config.icon}</span>
            <span className="booking-badge-label">{config.label}</span>
        </span>
    );
}
