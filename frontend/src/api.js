/**
 * Backend se baat karne ki ek hi jagah.
 *
 * Token strategy:
 *   ACCESS token  -> is module ke variable me (RAM). Page reload pe chala jata hai.
 *   REFRESH token -> httpOnly cookie me. JavaScript use chhoo bhi nahi sakti.
 *
 * localStorage me token kyu nahi: koi bhi XSS (ya koi bhi npm package)
 * localStorage padh sakta hai. RAM me rakha token page ke saath hi mar jata
 * hai, aur reload pe cookie se naya le lete hain.
 */

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

// Sirf memory me. Jaan-boojh ke localStorage me nahi.
let accessToken = null;

export function setAccessToken(token) {
  accessToken = token;
}

export function getAccessToken() {
  return accessToken;
}

/** Refresh cookie se naya access token. Login page pe bheje bina. */
async function tryRefresh() {
  const res = await fetch(`${API_URL}/api/auth/refresh`, {
    method: "POST",
    credentials: "include",     // cookie bhejne ke liye zaroori
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
    // Auth routes ko cookie chahiye. Baaki ko nahi, par bhejne me harj nahi.
    credentials: "include",
  });
}

/**
 * Saare requests yahin se.
 *
 * 401 aaya to EK BAAR refresh karke retry karte hain. Isse access token
 * beech kaam me expire ho jaye to bhi user ko pata hi nahi chalta —
 * usse dobara login nahi karna padta.
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
    // FastAPI errors { "detail": "..." } format me aate hain
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) {
        message =
          typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* response me JSON nahi tha */
    }
    const error = new Error(message);
    error.status = res.status;    // 409 ko UI me alag treat karna hai

    // Rate limit hone par server batata hai kitni der ruko
    if (res.status === 429) {
      error.retryAfter = Number(res.headers.get("Retry-After")) || null;
    }

    throw error;
  }

  // 204 No Content (logout) me body hoti hi nahi
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

/** Google login — full page redirect, fetch nahi (browser ko jaana hai) */
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

// ---- Admin ----

export const getAdminStats = () => request("/api/admin/stats");

// ---- Payments ----

/** Seat ke liye checkout session banao. Wapas checkout_url milta hai. */
export const startCheckout = (seatId) =>
  request("/api/payments/checkout", {
    method: "POST",
    body: JSON.stringify({ seat_id: seatId }),
  });

export const getPayment = (paymentId) => request(`/api/payments/${paymentId}`);

/** Sirf mock provider — asli gateway me ye webhook se hota hai. */
export const simulatePayment = (paymentId, outcome) =>
  request(`/api/payments/${paymentId}/simulate`, {
    method: "POST",
    body: JSON.stringify({ outcome }),
  });

// ---- Bookings ----

export const getMyBookings = () => request("/api/bookings");

/**
 * Seat book karo.
 *
 * `Idempotency-Key` bhejte hain taki double-click ya network retry se
 * do bookings na banein. Wahi key dubara jaaye to server naya kaam nahi
 * karta — pehla wala jawab wapas de deta hai.
 *
 * Key har ATTEMPT ke liye nayi banti hai, har seat ke liye nahi — matlab
 * ek hi confirm click ka retry safe hai, par user jaan-boojh ke dubara
 * book karna chahe to wo alag request hai.
 */
export const createBooking = (seatId, idempotencyKey = crypto.randomUUID()) =>
  request("/api/bookings", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ seat_id: seatId }),
  });

/**
 * Ticket PDF download.
 *
 * ⚠️ `request()` use nahi kar sakte — wo `res.json()` karta hai, aur yahan
 * binary blob chahiye.
 *
 * Aur `window.open` bhi kaam nahi karega: endpoint ko `Authorization`
 * header chahiye, par browser navigation me custom headers nahi jaate.
 * Isliye fetch karke blob banate hain aur ek chhupa hua <a> click karte hain.
 */
export async function downloadTicket(bookingId) {
  const res = await fetch(`${API_URL}/api/bookings/${bookingId}/ticket`, {
    headers: { Authorization: `Bearer ${getAccessToken()}` },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "Ticket download nahi hua");
  }

  return res.blob();
}

export const retryTicket = (bookingId) =>
  request(`/api/bookings/${bookingId}/ticket/retry`, { method: "POST" });

export const cancelBooking = (bookingId) =>
  request(`/api/bookings/${bookingId}`, { method: "DELETE" });

export { API_URL };
