import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import type { Session } from "@supabase/supabase-js";
import "./DashboardPage.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

async function getAuthHeaders(session: Session | null): Promise<Record<string, string>> {
  return session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};
}

interface Trip {
  id: string;
  name: string;
  trip_location?: string;
  trip_type?: string;
  created_at?: string;
}

const DAY_CHIPS = [2, 3, 5, 7, 10, 14];

export default function DashboardPage() {
  const { session, signOut } = useAuth();
  const navigate = useNavigate();
  const [trips, setTrips] = useState<Trip[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [groupLocation, setGroupLocation] = useState("");
  // Duration state
  const [durationMode, setDurationMode] = useState<"dates" | "days">("days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [selectedDays, setSelectedDays] = useState<number | null>(null);
  const [customDays, setCustomDays] = useState("");
  const [maxTravelers, setMaxTravelers] = useState<number>(6);
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const headers = await getAuthHeaders(session);
      try {
        const res = await fetch(`${API_BASE}/trips`, { headers });
        if (res.ok) setTrips(await res.json());
      } catch { /* ignore */ }
      setLoading(false);
    })();
  }, [session]);

  const openModal = () => {
    setGroupName("");
    setGroupLocation("");
    setDurationMode("days");
    setStartDate("");
    setEndDate("");
    setSelectedDays(null);
    setCustomDays("");
    setMaxTravelers(6);
    setFormError(null);
    setShowModal(true);
  };

  const closeModal = () => setShowModal(false);

  const autoDayCount = (): number | null => {
    if (!startDate || !endDate) return null;
    const ms = new Date(endDate).getTime() - new Date(startDate).getTime();
    const d = Math.round(ms / 86400000);
    return d > 0 ? d : null;
  };

  const createGroupTrip = async () => {
    if (!groupName.trim()) { setFormError("Trip name is required"); return; }
    let tripDays: number;
    if (durationMode === "dates") {
      const days = autoDayCount();
      if (!days) { setFormError("Please select valid start and end dates"); return; }
      tripDays = days;
    } else {
      if (selectedDays === null || selectedDays <= 0) {
        setFormError("Please select a duration");
        return;
      }
      tripDays = selectedDays;
    }

    setCreatingGroup(true);
    setFormError(null);
    try {
      const headers = { ...(await getAuthHeaders(session)), "Content-Type": "application/json" };
      const res = await fetch(`${API_BASE}/group-trips/create`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: groupName.trim(),
          trip_location: groupLocation.trim() || null,
          trip_days: tripDays,
          max_travelers: maxTravelers,
          ...(durationMode === "dates" && startDate ? { start_date: startDate } : {}),
          ...(durationMode === "dates" && endDate   ? { end_date: endDate }     : {}),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Failed to create trip" }));
        setFormError(err.detail || "Failed to create trip");
        return;
      }
      const data = await res.json();
      navigate(`/group/${data.group_id}/waiting`);
    } catch {
      setFormError("Network error");
    } finally {
      setCreatingGroup(false);
    }
  };

  const userInitial = session?.user?.email?.charAt(0).toUpperCase() ?? "?";

  return (
    <>
      {/* ── Sky atmosphere ── */}
      <div className="sky-bg" />
      <div className="sky-arc sky-arc-1" />
      <div className="sky-arc sky-arc-2" />
      <div className="sky-cloud sky-cloud-1" />
      <div className="sky-cloud sky-cloud-2" />
      <div className="sky-cloud sky-cloud-3" />
      <div className="sky-cloud sky-cloud-4" />
      <div className="sky-cloud sky-cloud-5" />

      {/* ── Wordmark ── */}
      <button
        className="sky-wordmark"
        onClick={() => navigate("/dashboard")}
        style={{ background: 'none', border: 'none', cursor: 'pointer' }}
      >
        <span className="sky-wordmark-star">✦</span>
        NovaSync
      </button>

      {/* ── User pill header ── */}
      <header className="dash-header">
        <div className="dash-user-pill">
          <div className="dash-avatar">{userInitial}</div>
        </div>
        <button onClick={signOut} className="sky-btn-danger">Sign out</button>
      </header>

      {/* ── Main content ── */}
      <main className="dash-main dashboard-root">
        <section style={{ marginBottom: 48 }}>
          <h2 className="dash-section-title">Plan a Trip</h2>
          <div className="dash-action-cards">
            <div className="glass-card dash-action-card" onClick={() => navigate("/plan")}>
              <div className="dash-action-card-icon">✈️</div>
              <h3>Individual Trip</h3>
              <p>Plan a trip crafted just for you</p>
            </div>
            <div className="glass-card dash-action-card" onClick={openModal}>
              <div className="dash-action-card-icon">🌍</div>
              <h3>Group Trip</h3>
              <p>Collaborate with friends</p>
            </div>
          </div>
        </section>

        <section>
          <h2 className="dash-section-title">My Trips</h2>
          {loading ? (
            <p className="dash-empty">Loading…</p>
          ) : trips.length === 0 ? (
            <p className="dash-empty">No trips yet. Create one above!</p>
          ) : (
            <div className="dash-trips-grid">
              {trips.map(trip => (
                <div
                  key={trip.id}
                  className="glass-card dash-trip-card"
                  onClick={() =>
                    trip.trip_type === "group"
                      ? navigate(`/group/${trip.id}/waiting`)
                      : navigate(`/trips/${trip.id}`)
                  }
                >
                  {trip.trip_type && (
                    <span className={`dash-trip-badge ${trip.trip_type}`}>{trip.trip_type}</span>
                  )}
                  <h3>{trip.name}</h3>
                  {trip.trip_location && <p>{trip.trip_location}</p>}
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      {/* ── Create Group Trip Modal ── */}
      {showModal && (
        <div className="dash-modal-overlay" onClick={e => { if (e.target === e.currentTarget) closeModal(); }}>
          <div className="glass-card dash-modal">
            <h3>Create Group Trip</h3>

            <div className="dash-modal-field">
              <label className="dash-modal-label">Trip Name</label>
              <input
                className="sky-input"
                placeholder="e.g. Tokyo with friends"
                value={groupName}
                onChange={e => setGroupName(e.target.value)}
              />
            </div>

            <div className="dash-modal-field">
              <label className="dash-modal-label">Destination (optional)</label>
              <input
                className="sky-input"
                placeholder="Where are you going?"
                value={groupLocation}
                onChange={e => setGroupLocation(e.target.value)}
              />
            </div>

            <div className="dash-modal-field">
              <label className="dash-modal-label">How long is the trip?</label>

              {/* Toggle */}
              <div className="duration-toggle">
                <span>Just days</span>
                <button
                  className={`duration-toggle-track ${durationMode === "dates" ? "active" : ""}`}
                  onClick={() => setDurationMode(m => m === "dates" ? "days" : "dates")}
                  aria-label="Toggle date mode"
                >
                  <span className="duration-toggle-thumb" />
                </button>
                <span>Have dates?</span>
              </div>

              {durationMode === "dates" ? (
                <>
                  <div className="duration-date-row">
                    <input
                      type="date"
                      className="sky-input"
                      value={startDate}
                      onChange={e => setStartDate(e.target.value)}
                    />
                    <input
                      type="date"
                      className="sky-input"
                      value={endDate}
                      onChange={e => setEndDate(e.target.value)}
                    />
                  </div>
                  {autoDayCount() !== null && (
                    <p className="duration-auto-label">{autoDayCount()} days calculated</p>
                  )}
                </>
              ) : (
                <div className="duration-chips">
                  {DAY_CHIPS.map(d => (
                    <button
                      key={d}
                      className={`duration-chip ${selectedDays === d ? "selected" : ""}`}
                      onClick={() => setSelectedDays(d)}
                    >
                      {d}d
                    </button>
                  ))}
                  <div className="duration-chip-custom">
                    <button
                      className={`duration-chip ${selectedDays === -1 ? "selected" : ""}`}
                      onClick={() => setSelectedDays(-1)}
                    >
                      Custom
                    </button>
                    {selectedDays === -1 && (
                      <input
                        type="number"
                        min={1}
                        max={90}
                        placeholder="days"
                        value={customDays}
                        onChange={e => {
                          setCustomDays(e.target.value);
                          const n = parseInt(e.target.value);
                          if (!isNaN(n) && n > 0) setSelectedDays(n);
                        }}
                      />
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="dash-modal-field">
              <label className="dash-modal-label">Group size limit</label>
              <div className="duration-chips">
                {[2, 3, 4, 5, 6, 8, 10, 15, 20].map(n => (
                  <button
                    key={n}
                    className={`duration-chip ${maxTravelers === n ? "selected" : ""}`}
                    onClick={() => setMaxTravelers(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <p style={{ fontSize: '0.77rem', color: '#8ba5c0', margin: '6px 0 0' }}>
                Max {maxTravelers} traveller{maxTravelers !== 1 ? 's' : ''} can join via the link
              </p>
            </div>

            {formError && <p className="dash-error">{formError}</p>}

            <div className="dash-modal-actions">
              <button onClick={closeModal} className="sky-btn-secondary">Cancel</button>
              <button onClick={createGroupTrip} disabled={creatingGroup} className="sky-btn-primary">
                {creatingGroup ? "Creating…" : "Create & Get Join Link"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
