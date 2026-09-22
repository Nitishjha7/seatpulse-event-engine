/**
 * Centralized API client.
 *
 * Access token lives in a module variable (cleared on reload); refresh token
 * lives in an httpOnly cookie, invisible to JS. Not using localStorage since
 * it's readable by any injected script — on reload we just fetch a fresh
 * access token using the cookie.
 */

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

// Stored in memory only. Intentionally avoiding localStorage.
let accessToken = null;

export function setAccessToken(token) {
  accessToken = token;
}

export function getAccessToken() {
  return accessToken;
}

/** Refresh access token using the cookie without redirecting to login. */
async function tryRefresh() {
  const res = await fetch(`${API_URL}/api/auth/refresh`, {
    method: "POST",
    credentials: "include",     // Required to send cookies
  });
  if (!res.ok) return null;

  const data = await res.json();
  accessToken = data.access_token;
  return data;
}

async function rawRequest(path, options, token) {
  return fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
    // Auth routes require cookies; others don't, but sending them is safe.
    credentials: "include",
  });
}

/** On a 401, tries one refresh + retry so an expired token doesn't interrupt the session. */
async function request(path, options = {}, { retry = true } = {}) {
  let res = await rawRequest(path, options, accessToken);

  if (res.status === 401 && retry && !path.startsWith("/api/auth/")) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      res = await rawRequest(path, options, accessToken);
    }
  }

  if (!res.ok) {
    // FastAPI errors follow { "detail": "..." } format
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) {
        message =
          typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* Response was not JSON */
    }
    const error = new Error(message);
    error.status = res.status;    // 409 requires specific UI handling

    // Handle rate limiting
    if (res.status === 429) {
      error.retryAfter = Number(res.headers.get("Retry-After")) || null;
    }

    throw error;
  }

  // 204 No Content (e.g., logout) has no body
  if (res.status === 204) return null;
  return res.json();
}

// ---- Auth ----

export const getAuthConfig = () => request("/api/auth/config");

export const register = (email, password, fullName) =>
  request("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name: fullName || null }),
  });

export const login = (email, password) =>
  request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

export const refreshSession = tryRefresh;

export const logout = () => request("/api/auth/logout", { method: "POST" });

export const getMe = () => request("/api/auth/me");

/** Google login — full page redirect (browser-handled) */
export const googleLoginUrl = () => `${API_URL}/api/auth/google/login`;

// ---- Events / Seats ----

export const getHealth = () => request("/api/health");
export const getEvents = () => request("/api/events");
export const getEvent = (eventId) => request(`/api/events/${eventId}`);
export const getEventSeats = (eventId) => request(`/api/events/${eventId}/seats`);

// ---- Seat locking ----

export const lockSeat = (seatId) =>
  request(`/api/seats/${seatId}/lock`, { method: "POST" });

export const unlockSeat = (seatId) =>
  request(`/api/seats/${seatId}/lock`, { method: "DELETE" });

// ---- Organizer ----

export const getMyEvents = () => request("/api/organizer/events");

/** 404 means "not enough bookings yet" — a normal, expected outcome for a new event. */
export const getForecast = (eventId) =>
  request(`/api/organizer/events/${eventId}/forecast`);

/** Drafts event fields from a brief — doesn't persist anything, just pre-fills the form. */
export const draftEvent = (brief) =>
  request("/api/organizer/events/draft", {
    method: "POST",
    body: JSON.stringify({ brief }),
  });

/**
 * Returns a poster as a blob URL rather than JSON — the response body is
 * raw image bytes, so this bypasses request()'s res.json() and handles
 * refresh/error the same way by hand.
 */
export async function generatePoster(brief) {
  const path = "/api/organizer/events/poster";
  const options = { method: "POST", body: JSON.stringify({ brief }) };

  let res = await rawRequest(path, options, accessToken);
  if (res.status === 401) {
    const refreshed = await tryRefresh();
    if (refreshed) res = await rawRequest(path, options, accessToken);
  }

  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) message = body.detail;
    } catch {
      /* Response was not JSON */
    }
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }

  return URL.createObjectURL(await res.blob());
}

export const createEvent = (payload) =>
  request("/api/organizer/events", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateEvent = (eventId, payload) =>
  request(`/api/organizer/events/${eventId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteEvent = (eventId) =>
  request(`/api/organizer/events/${eventId}`, { method: "DELETE" });

// ---- Gate check-in ----

/** Check in via QR token. Returns 200 even on failure (`ok: false`) — check that field, not try/catch. */
export const checkIn = (token) =>
  request("/api/checkin", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const getCheckinStats = (eventId) =>
  request(`/api/checkin/events/${eventId}/stats`);

// ---- Group booking (split payment) ----

/** Holds N seats and returns a `share_token` (not an id, to avoid enumeration). */
export const createGroup = (seatIds, deadlineMinutes) =>
  request("/api/groups", {
    method: "POST",
    body: JSON.stringify({ seat_ids: seatIds, deadline_minutes: deadlineMinutes }),
  });

export const getGroup = (shareToken) => request(`/api/groups/${shareToken}`);

export const getMyGroups = () => request("/api/groups");

export const claimShare = (shareToken, shareId) =>
  request(`/api/groups/${shareToken}/shares/${shareId}/claim`, { method: "POST" });

export const payShare = (shareToken, shareId) =>
  request(`/api/groups/${shareToken}/shares/${shareId}/pay`, { method: "POST" });

export const cancelGroup = (shareToken) =>
  request(`/api/groups/${shareToken}`, { method: "DELETE" });

// ---- Seat search ----

/** Search seats via natural language (`query`, needs GEMINI_API_KEY server-side) or `filters`. */
export const searchSeats = (eventId, body) =>
  request(`/api/events/${eventId}/seats/search`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// ---- Admin ----

export const getAdminStats = () => request("/api/admin/stats");

// ---- Payments ----

/** Create checkout session. Returns `checkout_url`. */
export const startCheckout = (seatId) =>
  request("/api/payments/checkout", {
    method: "POST",
    body: JSON.stringify({ seat_id: seatId }),
  });

export const getPayment = (paymentId) => request(`/api/payments/${paymentId}`);

/** Mock provider for testing; production uses webhooks. */
export const simulatePayment = (paymentId, outcome) =>
  request(`/api/payments/${paymentId}/simulate`, {
    method: "POST",
    body: JSON.stringify({ outcome }),
  });

// ---- Bookings ----

export const getMyBookings = () => request("/api/bookings");

/** Books a seat. `Idempotency-Key` per attempt keeps retries from double-booking. */
export const createBooking = (seatId, idempotencyKey = crypto.randomUUID()) =>
  request("/api/bookings", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ seat_id: seatId }),
  });

/**
 * Downloads the ticket PDF. Can't use `request()` (expects JSON, this is a
 * blob) or a plain link (needs an auth header), so we fetch and click a hidden <a>.
 */
export async function downloadTicket(bookingId) {
  const res = await fetch(`${API_URL}/api/bookings/${bookingId}/ticket`, {
    headers: { Authorization: `Bearer ${getAccessToken()}` },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "Ticket download failed");
  }

  return res.blob();
}

export const retryTicket = (bookingId) =>
  request(`/api/bookings/${bookingId}/ticket/retry`, { method: "POST" });

export const cancelBooking = (bookingId) =>
  request(`/api/bookings/${bookingId}`, { method: "DELETE" });

export { API_URL };
