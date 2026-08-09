/**
 * Backend se baat karne ki ek hi jagah.
 *
 * Kyu alag file: URL har component me likhoge to deploy ke waqt 20 jagah
 * badalna padega. Yahan ek jagah hai, aur wo bhi .env se aata hai.
 *
 * Vite me sirf VITE_ se shuru hone wale variables hi frontend code tak
 * pahunchte hain — ye jaan-boojh ke hai, taki galti se koi secret
 * browser me na chala jaye.
 */

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

/** Backend zinda hai ya nahi. */
export async function getHealth() {
  const res = await fetch(`${API_URL}/api/health`);
  if (!res.ok) {
    throw new Error(`Backend ne ${res.status} return kiya`);
  }
  return res.json();
}

export { API_URL };
