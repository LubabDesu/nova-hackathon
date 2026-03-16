import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import type { TravelerParticipant } from "../types";
import "../styles/sky-theme.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

interface GroupStatus {
  group_id: string;
  name?: string;
  destination?: string;
  trip_location?: string;
  participants: TravelerParticipant[];
  slots_remaining?: number;
  max_travelers?: number;
}

export default function GroupWaitingPage() {
  const { groupId } = useParams<{ groupId: string }>();
  const { session } = useAuth();
  const navigate = useNavigate();

  const [status, setStatus] = useState<GroupStatus | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [copyMsg, setCopyMsg] = useState("");

  const joinUrl = groupId
    ? `${window.location.origin}/join/${groupId}`
    : "";

  // Poll for group status every 5 seconds
  useEffect(() => {
    if (!groupId) return;
    let cancelled = false;

    const fetchStatus = async () => {
      const token = session?.access_token;
      const headers: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};
      try {
        const res = await fetch(`${API_BASE}/group-trips/${groupId}/status`, { headers });
        if (res.ok && !cancelled) {
          const data: GroupStatus = await res.json();
          setStatus(data);
        }
      } catch { /* ignore */ }
    };

    void fetchStatus();
    const interval = setInterval(() => { void fetchStatus(); }, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [groupId, session]);

  const handlePlanNow = () => {
    navigate(`/group/${groupId}/plan`);
  };

  return (
    <>
      <div className="sky-bg" />
      <div className="sky-arc sky-arc-1" />
      <div className="sky-arc sky-arc-2" />
      <div className="sky-cloud sky-cloud-1" />
      <div className="sky-cloud sky-cloud-2" />
      <div className="sky-cloud sky-cloud-3" />
      <div className="sky-cloud sky-cloud-4" />
      <div className="sky-cloud sky-cloud-5" />
      <button
        className="sky-wordmark"
        onClick={() => navigate("/dashboard")}
        style={{ background: 'none', border: 'none', cursor: 'pointer' }}
      >
        <span className="sky-wordmark-star">✦</span>
        NovaSync
      </button>
      <div className="group-waiting-page">
        {/* ── Trip title ── */}
        <div style={{ marginBottom: 20 }}>
          <button
            onClick={() => navigate("/dashboard")}
            style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5, color: '#5a7a9a', fontSize: '0.82rem', padding: 0, marginBottom: 10, fontFamily: "'DM Sans', sans-serif", letterSpacing: '0.01em' }}
          >
            ← dashboard
          </button>
          <h1 style={{ fontFamily: "'Cormorant Garamond', serif", color: '#0b1f38', fontSize: '1.9rem', fontWeight: 600, margin: 0, lineHeight: 1.2 }}>
            {status?.name ?? "Group Trip"}
          </h1>
          {(status?.destination || status?.trip_location) && (
            <p style={{ margin: '4px 0 0', color: '#5a7a9a', fontSize: '0.88rem', fontFamily: "'DM Sans', sans-serif" }}>
              {status.destination ?? status.trip_location}
            </p>
          )}
        </div>

        {/* ── Unified panel ── */}
        <div style={{
          background: 'rgba(255,255,255,0.55)',
          backdropFilter: 'blur(28px) saturate(1.5)',
          WebkitBackdropFilter: 'blur(28px) saturate(1.5)',
          border: '1px solid rgba(255,255,255,0.85)',
          borderRadius: 16,
          boxShadow: '0 2px 0 rgba(255,255,255,0.8) inset, 0 6px 24px rgba(90,145,200,0.12)',
          overflow: 'hidden',
          marginBottom: 12,
        }}>
          {/* Share link row */}
          <div style={{ padding: '14px 18px', borderBottom: '1px solid rgba(74,141,196,0.1)' }}>
            <p style={{ color: '#5a7a9a', marginBottom: 7, fontSize: '0.78rem', fontFamily: "'DM Sans', sans-serif", textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Share with your group
            </p>
            <div className="join-link-row">
              <input readOnly value={joinUrl} className="sky-input join-link-input" style={{ fontSize: '0.85rem', padding: '8px 12px' }} />
              <button
                className="sky-btn-secondary"
                style={{ padding: '7px 18px', fontSize: '0.85rem', flexShrink: 0 }}
                onClick={() => {
                  navigator.clipboard.writeText(joinUrl).then(() => {
                    setCopyMsg("Copied!");
                    setTimeout(() => setCopyMsg(""), 2000);
                  });
                }}
              >
                {copyMsg || "Copy"}
              </button>
            </div>
          </div>

          {/* Participants */}
          <div style={{ padding: '14px 18px', borderBottom: '1px solid rgba(74,141,196,0.1)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <span style={{ fontFamily: "'DM Sans', sans-serif", fontWeight: 600, fontSize: '0.88rem', color: '#1e3a5f' }}>
                Participants
              </span>
              <span style={{ fontSize: '0.75rem', color: '#4a6a8a', background: 'rgba(74,141,196,0.1)', border: '1px solid rgba(74,141,196,0.2)', borderRadius: 999, padding: '2px 9px', fontFamily: "'DM Sans', sans-serif" }}>
                {status?.participants.length ?? 0}{status?.max_travelers !== undefined ? ` / ${status.max_travelers}` : ''} joined
              </span>
            </div>

            {status?.participants && status.participants.length > 0 ? (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {status.participants.map((p, i) => {
                  const dir = p.input_directives;
                  const tags: string[] = [];
                  if (dir?.budget_level) tags.push(dir.budget_level.replace('_', ' '));
                  if (dir?.pace) tags.push(dir.pace);
                  if (dir?.dietary?.length) tags.push(...dir.dietary);
                  if (dir?.wake_time_pref) tags.push(dir.wake_time_pref.replace('_', ' '));

                  return (
                    <li key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0', borderBottom: i < (status.participants.length - 1) ? '1px solid rgba(74,141,196,0.07)' : 'none' }}>
                      <span style={{ width: 26, height: 26, borderRadius: '50%', background: `hsl(${(i * 67 + 180) % 360}, 50%, 88%)`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.72rem', fontWeight: 700, color: `hsl(${(i * 67 + 180) % 360}, 45%, 30%)`, flexShrink: 0 }}>
                        {p.nickname.charAt(0).toUpperCase()}
                      </span>
                      <span style={{ fontWeight: 500, color: '#0b1f38', fontSize: '0.9rem', fontFamily: "'DM Sans', sans-serif", flex: 1 }}>{p.nickname}</span>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, justifyContent: 'flex-end' }}>
                        {tags.slice(0, 3).map((tag, t) => (
                          <span key={t} style={{ fontSize: '0.68rem', padding: '1px 8px', borderRadius: 99, background: 'rgba(74,141,196,0.09)', color: '#1e5a96', border: '1px solid rgba(74,141,196,0.18)', fontFamily: "'DM Sans', sans-serif" }}>
                            {tag}
                          </span>
                        ))}
                      </div>
                      <span style={{ fontSize: '0.72rem', color: '#9fb3c8', whiteSpace: 'nowrap', fontFamily: "'DM Sans', sans-serif" }}>
                        {new Date(p.submitted_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p style={{ color: '#8ba5c0', fontStyle: 'italic', fontSize: '0.85rem', margin: 0, fontFamily: "'DM Sans', sans-serif" }}>
                No participants yet — share the link above
              </p>
            )}
          </div>

          {/* Add preferences + Plan Now */}
          <div style={{ padding: '12px 18px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <button
              className="sky-btn-secondary"
              style={{ padding: '7px 16px', fontSize: '0.85rem' }}
              onClick={() => navigate(`/join/${groupId}`)}
            >
              Add your preferences →
            </button>
            <button
              className="sky-btn-primary"
              style={{ padding: '9px 24px', fontSize: '0.92rem', borderRadius: 10 }}
              onClick={handlePlanNow}
              disabled={(status?.participants.length ?? 0) === 0}
            >
              Plan Now ✈️
            </button>
          </div>
        </div>

        {planError && <p style={{ color: '#c0392b', fontSize: '0.85rem', fontFamily: "'DM Sans', sans-serif", marginTop: 6 }}>{planError}</p>}
      </div>
    </>
  );
}
