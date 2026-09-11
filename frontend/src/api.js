/**
 * Centralized API communication module.
 *
 * Token strategy:
 *   ACCESS token  -> Stored in module variable (RAM). Cleared on page reload.
 *   REFRESH token -> Stored in httpOnly cookie. Inaccessible to JavaScript.
 *
 * Why not localStorage: Vulnerable to XSS. RAM-based tokens expire on reload,
 * at which point we fetch a new one using the secure cookie.
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

/**
 * Centralized request handler.
 *
 * On 401, attempts one refresh and retries. This allows seamless session
 * recovery if the access token expires during use.
 */
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

/**
 * Draft event listing from a brief.
 *
 * Does not persist data; returns suggestions to pre-fill the form.
 * Publishing is handled separately by the organizer.
 */
export const draftEvent = (brief) =>
  request("/api/organizer/events/draft", {
    method: "POST",
    body: JSON.stringify({ brief }),
  });

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

/**
 * Mark entry via QR token.
 *
 * ⚠️ Returns 200 even if check-in fails (with `ok: false`).
 * Check `result.ok` instead of relying on try/catch.
 */
export const checkIn = (token) =>
  request("/api/checkin", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const getCheckinStats = (eventId) =>
  request(`/api/checkin/events/${eventId}/stats`);

// ---- Group booking (split payment) ----

/**
 * Hold N seats and generate a shareable link.
 *
 * Returns a `share_token` instead of an `id` to prevent enumeration attacks.
 */
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

// ---- Seat search (Phase 19) ----

/**
 * Search seats via natural language or filters.
 *
 * `query` requires GEMINI_API_KEY on the server.
 * `filters` are always available.
 */
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

/**
 * Book a seat.
 *
 * Uses `Idempotency-Key` to prevent duplicate bookings from retries.
 * The key is unique per attempt, ensuring safe retries for the same action.
 */
export const createBooking = (seatId, idempotencyKey = crypto.randomUUID()) =>
  request("/api/bookings", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ seat_id: seatId }),
  });

/**
 * Download ticket PDF.
 *
 * ⚠️ Cannot use `request()` as it expects JSON; we need a binary blob.
 * Browser navigation doesn't support custom headers, so we fetch the blob
 * and trigger a hidden <a> click.
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
