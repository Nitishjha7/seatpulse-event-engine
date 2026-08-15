/**
 * Backend se baat karne ki ek hi jagah.
 *
 * Vite me sirf VITE_ se shuru hone wale variables hi frontend code tak
 * pahunchte hain — taki galti se koi secret browser me na chala jaye.
 */

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

/**
 * Saare requests yahin se jaate hain.
 * Ek jagah error handling isliye — har call me try/catch aur res.ok
 * likhna nahi padta, aur backend ka error message UI tak pahunch jata hai.
 */
async function request(path, options = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    // FastAPI errors { "detail": "..." } format me aate hain
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) {
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // response me JSON nahi tha — upar wala message hi chalega
    }
    const error = new Error(message);
    error.status = res.status;   // 409 ko alag treat karna hai UI me
    throw error;
  }

  return res.json();
}

export const getHealth = () => request("/api/health");
export const getMe = () => request("/api/me");

export const getEvents = () => request("/api/events");
export const getEvent = (eventId) => request(`/api/events/${eventId}`);
export const getEventSeats = (eventId) => request(`/api/events/${eventId}/seats`);

export const getMyBookings = (userId) => request(`/api/bookings?user_id=${userId}`);

export const createBooking = (seatId, userId) =>
  request("/api/bookings", {
    method: "POST",
    body: JSON.stringify({ seat_id: seatId, user_id: userId }),
  });

export const cancelBooking = (bookingId) =>
  request(`/api/bookings/${bookingId}`, { method: "DELETE" });

export { API_URL };
