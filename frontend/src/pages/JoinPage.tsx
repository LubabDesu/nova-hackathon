import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useDropzone } from "react-dropzone";
import { supabase } from "../lib/supabase";
import { useAuth } from "../contexts/AuthContext";
import "../styles/sky-theme.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

interface InputDirectives {
  hard_constraints: string[];
  soft_preferences: string[];
  must_include: string[];
  avoid: string[];
  travel_party: string[];
  dietary: string[];
  budget_level?: string;
  pace?: string;
  wake_time_pref?: string;
  fitness_level?: string;
  accommodation_style?: string;
  mobility_mode?: string;
  inspiration_links: string[];
}

const emptyDirectives = (): InputDirectives => ({
  hard_constraints: [], soft_preferences: [], must_include: [], avoid: [],
  travel_party: [], dietary: [], inspiration_links: [],
});

// ── Inspiration dropzone sub-component ──────────────────────────────────────
interface InspirationZoneProps {
  links: string[];
  onAdd: (url: string) => void;
  onRemove: (url: string) => void;
  groupId: string;
}

function InspirationZone({ links, onAdd, onRemove, groupId }: InspirationZoneProps) {
  const [urlInput, setUrlInput] = useState("");
  const [urlError, setUrlError] = useState("");
  const [uploading, setUploading] = useState(false);

  const addUrl = () => {
    const trimmed = urlInput.trim();
    if (!trimmed) return;
    try { new URL(trimmed); } catch {
      setUrlError("Please enter a valid URL");
      return;
    }
    if (links.includes(trimmed)) { setUrlError("Already added"); return; }
    onAdd(trimmed);
    setUrlInput("");
    setUrlError("");
  };

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    setUploading(true);
    for (const file of acceptedFiles) {
      const path = `group/${groupId}/${Date.now()}-${file.name.replace(/\s+/g, "_")}`;
      const { data, error } = await supabase.storage
        .from("inspiration")
        .upload(path, file, { contentType: file.type, upsert: false });
      if (!error && data) {
        const { data: urlData } = supabase.storage.from("inspiration").getPublicUrl(data.path);
        if (urlData.publicUrl) onAdd(urlData.publicUrl);
      }
    }
    setUploading(false);
  }, [groupId, onAdd]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "image/*": [".jpeg", ".jpg", ".png", ".webp", ".gif"] },
    maxSize: 8 * 1024 * 1024, // 8MB
  });

  // Detect if a link is an image URL (uploaded to Supabase Storage or ends in image ext)
  const isImageUrl = (url: string) =>
    url.includes("supabase") || /\.(jpg|jpeg|png|webp|gif)(\?|$)/i.test(url);

  return (
    <div style={{ marginTop: 4 }}>
      {/* URL input row */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <input
          className="sky-input"
          style={{ flex: 1 }}
          placeholder="Paste a link — Instagram, TikTok, blog, map…"
          value={urlInput}
          onChange={e => { setUrlInput(e.target.value); setUrlError(""); }}
          onKeyDown={e => e.key === "Enter" && addUrl()}
        />
        <button
          type="button"
          onClick={addUrl}
          className="sky-btn-secondary"
          style={{ whiteSpace: 'nowrap', padding: '10px 18px', borderRadius: 10 }}
        >
          Add
        </button>
      </div>
      {urlError && <p style={{ color: '#c0392b', fontSize: '0.8rem', margin: '0 0 8px' }}>{urlError}</p>}

      {/* Image dropzone */}
      <div
        {...getRootProps()}
        style={{
          border: `1.5px dashed ${isDragActive ? '#2a6aa8' : 'rgba(100,155,200,0.3)'}`,
          borderRadius: 12,
          padding: '20px 16px',
          textAlign: 'center',
          cursor: 'pointer',
          background: isDragActive ? 'rgba(74,141,196,0.06)' : '#f2f6fa',
          transition: 'all 0.15s',
          marginBottom: links.length ? 12 : 0,
        }}
      >
        <input {...getInputProps()} />
        {uploading ? (
          <p style={{ color: '#4a8dc4', fontSize: '0.85rem', margin: 0 }}>Uploading…</p>
        ) : isDragActive ? (
          <p style={{ color: '#2a6aa8', fontSize: '0.85rem', margin: 0 }}>Drop to add!</p>
        ) : (
          <>
            <p style={{ color: '#4a6a8a', fontSize: '0.85rem', margin: '0 0 2px', fontWeight: 500 }}>
              📷 Drop inspiration images here
            </p>
            <p style={{ color: '#8ba5c0', fontSize: '0.77rem', margin: 0 }}>JPG, PNG, WEBP · max 8MB</p>
          </>
        )}
      </div>

      {/* Added links preview */}
      {links.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {links.map((url, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: 'rgba(74,141,196,0.08)',
                border: '1px solid rgba(74,141,196,0.25)',
                borderRadius: 10,
                padding: isImageUrl(url) ? '4px 8px 4px 4px' : '5px 8px 5px 10px',
                maxWidth: '100%',
              }}
            >
              {isImageUrl(url) ? (
                <img
                  src={url}
                  alt="inspiration"
                  style={{ width: 36, height: 36, objectFit: 'cover', borderRadius: 6, flexShrink: 0 }}
                />
              ) : (
                <span style={{ fontSize: '0.75rem', color: '#2a6aa8', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  🔗 {new URL(url).hostname}
                </span>
              )}
              <button
                type="button"
                onClick={() => onRemove(url)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#8ba5c0', fontSize: '0.8rem', padding: '0 2px', lineHeight: 1, flexShrink: 0 }}
                aria-label="Remove"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main JoinPage ────────────────────────────────────────────────────────────
export default function JoinPage() {
  const { groupId } = useParams<{ groupId: string }>();
  const navigate = useNavigate();
  const { session } = useAuth();
  const [trip, setTrip] = useState<{ name: string; trip_location?: string } | null>(null);
  const [nickname, setNickname] = useState("");
  const [freeText, setFreeText] = useState("");
  const [directives, setDirectives] = useState<InputDirectives>(emptyDirectives());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!groupId) return;
    fetch(`${API_BASE}/group-trips/${groupId}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setTrip(data); })
      .catch(() => setError("Could not load trip info"));
  }, [groupId]);

  const toggleArrayField = (field: "dietary" | "travel_party", value: string) => {
    setDirectives(d => {
      const arr = d[field];
      return { ...d, [field]: arr.includes(value) ? arr.filter(x => x !== value) : [...arr, value] };
    });
  };

  const setScalarField = (field: keyof InputDirectives, value: string) => {
    setDirectives(d => ({ ...d, [field]: (d[field] as string | undefined) === value ? undefined : value }));
  };

  const addInspirationLink = useCallback((url: string) => {
    setDirectives(d => ({ ...d, inspiration_links: [...d.inspiration_links, url] }));
  }, []);

  const removeInspirationLink = useCallback((url: string) => {
    setDirectives(d => ({ ...d, inspiration_links: d.inspiration_links.filter(u => u !== url) }));
  }, []);

  const handleSubmit = async () => {
    if (!nickname.trim()) { setError("Please enter your name"); return; }
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/group-trips/${groupId}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nickname: nickname.trim(), free_text: freeText, input_directives: directives }),
      });
      if (res.ok) {
        setSubmitted(true);
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Failed to join trip");
      }
    } catch { setError("Network error"); }
    setSubmitting(false);
  };

  if (submitted) {
    return (
      <>
        <div className="sky-bg" />
        <div className="sky-arc sky-arc-1" />
        <div className="sky-arc sky-arc-2" />
        <div className="sky-cloud sky-cloud-1" />
        <div className="sky-cloud sky-cloud-2" />
        <div className="sky-cloud sky-cloud-3" />
        <button
          className="sky-wordmark"
          onClick={() => navigate(session ? "/dashboard" : "/")}
          style={{ background: 'none', border: 'none', cursor: 'pointer' }}
        >
          <span className="sky-wordmark-star">✦</span>
          NovaSync
        </button>
        <div className="join-page">
          {session ? (
            /* Authenticated creator: offer back button */
            <div className="join-success">
              <div style={{ fontSize: '3rem', marginBottom: 16 }}>✈️</div>
              <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: '2.2rem', color: '#0b1f38', marginBottom: 8 }}>You're in!</h2>
              <p style={{ color: '#6b82a0', maxWidth: 320, margin: '0 auto 24px' }}>Your preferences have been submitted. Head back to start planning.</p>
              {groupId && (
                <button
                  className="sky-btn-secondary"
                  onClick={() => navigate(`/group/${groupId}/waiting`)}
                >
                  ← Back to trip
                </button>
              )}
            </div>
          ) : (
            /* Anonymous joiner: full "waiting for organiser" screen */
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '70vh', textAlign: 'center', padding: '0 24px' }}>
              <div style={{ fontSize: '4rem', marginBottom: 24 }}>🌍</div>
              <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: '2.6rem', fontWeight: 600, color: '#0b1f38', marginBottom: 12, lineHeight: 1.15 }}>
                You're in!
              </h2>
              {trip && (
                <p style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: '1.25rem', color: '#2a6aa8', marginBottom: 20, fontWeight: 500 }}>
                  Waiting for <em>{trip.name}</em>'s organiser to start planning
                </p>
              )}
              <p style={{ color: '#6b82a0', maxWidth: 360, margin: '0 auto 36px', fontSize: '0.95rem', lineHeight: 1.6 }}>
                Your preferences have been submitted. Hang tight — the itinerary is on its way!
              </p>
              {/* Animated pulse dots */}
              <div style={{ display: 'flex', gap: 10, marginBottom: 48 }}>
                {[0, 1, 2].map(i => (
                  <span
                    key={i}
                    style={{
                      width: 10, height: 10, borderRadius: '50%',
                      background: '#4a8dc4',
                      display: 'inline-block',
                      animation: `pulse-dot 1.4s ease-in-out ${i * 0.22}s infinite`,
                      opacity: 0.7,
                    }}
                  />
                ))}
              </div>
              <style>{`
                @keyframes pulse-dot {
                  0%, 80%, 100% { transform: scale(0.7); opacity: 0.4; }
                  40% { transform: scale(1.2); opacity: 1; }
                }
              `}</style>
              <a
                href="/"
                style={{ fontSize: '0.82rem', color: '#8ba5c0', textDecoration: 'none', borderBottom: '1px solid rgba(139,165,192,0.4)', paddingBottom: 2 }}
              >
                Want to plan your own trips? Create an account →
              </a>
            </div>
          )}
        </div>
      </>
    );
  }

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
        onClick={() => navigate(session ? "/dashboard" : "/")}
        style={{ background: 'none', border: 'none', cursor: 'pointer' }}
      >
        <span className="sky-wordmark-star">✦</span>
        NovaSync
      </button>

      <div className="join-page">
        <div className="join-card">
          <p className="join-eyebrow">✦ You're invited</p>
          <h1>Join Group Trip</h1>
          {trip && <p className="trip-title">{trip.name}{trip.trip_location ? ` · ${trip.trip_location}` : ""}</p>}
          <div className="join-form-divider" />

          <div className="form-group">
            <label>Your name</label>
            <input value={nickname} onChange={e => setNickname(e.target.value)} placeholder="What should we call you?" className="sky-input" />
          </div>

          <div className="form-group">
            <label>Travel notes (optional)</label>
            <textarea value={freeText} onChange={e => setFreeText(e.target.value)} placeholder="Any preferences, restrictions, or must-sees?" className="sky-input" rows={3} style={{ resize: 'vertical' }} />
          </div>

          <div className="form-group">
            <label>Budget</label>
            <div className="chip-group">
              {["budget", "mid_range", "luxury"].map(b => (
                <button key={b} className={`chip ${directives.budget_level === b ? "chip-active" : ""}`} onClick={() => setScalarField("budget_level", b)}>
                  {b.replace("_", " ")}
                </button>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label>Pace</label>
            <div className="chip-group">
              {["relaxed", "moderate", "active"].map(p => (
                <button key={p} className={`chip ${directives.pace === p ? "chip-active" : ""}`} onClick={() => setScalarField("pace", p)}>{p}</button>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label>Dietary</label>
            <div className="chip-group">
              {["vegetarian", "vegan", "halal", "kosher", "gluten-free"].map(diet => (
                <button key={diet} className={`chip ${directives.dietary.includes(diet) ? "chip-active" : ""}`} onClick={() => toggleArrayField("dietary", diet)}>{diet}</button>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label>Wake time</label>
            <div className="chip-group">
              {[["early_bird", "Early bird"], ["standard", "Standard"], ["late_riser", "Late riser"]].map(([val, label]) => (
                <button key={val} className={`chip ${directives.wake_time_pref === val ? "chip-active" : ""}`} onClick={() => setScalarField("wake_time_pref", val)}>{label}</button>
              ))}
            </div>
          </div>

          {/* ── Inspiration section ── */}
          <div className="form-group" style={{ borderTop: '1px solid rgba(74,141,196,0.12)', paddingTop: 24, marginTop: 8 }}>
            <label>
              ✦ Inspiration <span style={{ fontWeight: 400, letterSpacing: '0.04em', textTransform: 'none', color: '#8ba5c0', fontSize: '0.72rem' }}>optional</span>
            </label>
            <p style={{ color: '#6b82a0', fontSize: '0.82rem', margin: '-4px 0 12px', lineHeight: 1.6 }}>
              Share links or photos that capture the vibe — Instagram reels, blog posts, Google Maps spots, anything.
            </p>
            {groupId && (
              <InspirationZone
                links={directives.inspiration_links}
                onAdd={addInspirationLink}
                onRemove={removeInspirationLink}
                groupId={groupId}
              />
            )}
          </div>

          {error && <p className="error-msg">{error}</p>}
          <button onClick={handleSubmit} disabled={submitting} className="sky-btn-primary btn-full" style={{ marginTop: 20 }}>
            {submitting ? "Submitting…" : "Submit Preferences"}
          </button>
        </div>
      </div>
    </>
  );
}
