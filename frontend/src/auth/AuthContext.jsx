import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import * as api from '../api'

const AuthContext = createContext(null)

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}

/**
 * Centralized authentication state.
 *
 * Access tokens are stored in memory (api.js) and cleared on page reload.
 * We trigger a refresh on mount to restore the session via HTTP-only cookies.
 * This also handles post-Google login redirects where the backend sets the
 * cookie and redirects the user back to the app.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [googleEnabled, setGoogleEnabled] = useState(false)
  // Determines if the AI search feature is enabled based on server configuration.
  const [aiSearchEnabled, setAiSearchEnabled] = useState(false)

  const refreshTimer = useRef(null)

  /**
   * Silently refreshes the access token 1 minute before expiration.
   *
   * Prevents 401 errors during active sessions (e.g., while booking).
   */
  const scheduleRefresh = useCallback((expiresIn) => {
    clearTimeout(refreshTimer.current)
    const delay = Math.max((expiresIn - 60) * 1000, 10_000)

    refreshTimer.current = setTimeout(async () => {
      const data = await api.refreshSession()
      if (data) {
        setUser(data.user)
        scheduleRefresh(data.expires_in)
      } else {
        setUser(null)
      }
    }, delay)
  }, [])

  const applySession = useCallback(
    (data) => {
      api.setAccessToken(data.access_token)
      setUser(data.user)
      scheduleRefresh(data.expires_in)
    },
    [scheduleRefresh],
  )

  // On mount: Fetch auth config and attempt to restore session.
  useEffect(() => {
    let cancelled = false

    async function boot() {
      try {
        const config = await api.getAuthConfig()
        if (!cancelled) {
          setGoogleEnabled(config.google_enabled)
          setAiSearchEnabled(config.ai_search_enabled)
        }
      } catch {
        /* Backend unavailable; health check will handle error display */
      }

      try {
        const data = await api.refreshSession()
        if (data && !cancelled) applySession(data)
      } catch {
        /* No valid session cookie; user remains unauthenticated */
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    boot()
    return () => {
      cancelled = true
      clearTimeout(refreshTimer.current)
    }
  }, [applySession])

  // Clean up URL parameters after Google authentication callback.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.has('auth') || params.has('auth_error')) {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [])

  const value = {
    user,
    loading,
    googleEnabled,
    aiSearchEnabled,
    isAuthenticated: !!user,

    async login(email, password) {
      applySession(await api.login(email, password))
    },

    async register(email, password, fullName) {
      applySession(await api.register(email, password, fullName))
    },

    async logout() {
      try {
        await api.logout()
      } catch {
        /* Proceed with client-side logout even if server request fails */
      }
      clearTimeout(refreshTimer.current)
      api.setAccessToken(null)
      setUser(null)
    },

    googleLogin() {
      // Full page redirect to initiate OAuth flow.
      window.location.href = api.googleLoginUrl()
    },
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
