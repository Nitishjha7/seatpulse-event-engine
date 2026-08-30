import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import * as api from '../api'

const AuthContext = createContext(null)

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth ko AuthProvider ke andar hi use karo')
  return ctx
}

/**
 * Login state ek jagah.
 *
 * Access token RAM me hai (api.js me), isliye page reload pe chala jata hai.
 * Isliye mount hote hi ek `refresh()` maarte hain — refresh cookie valid ho
 * to user turant wapas logged in ho jata hai, use pata bhi nahi chalta.
 *
 * Yahi mechanism Google login ke baad bhi kaam aata hai: backend cookie set
 * karke frontend pe redirect karta hai, aur ye mount-refresh session bana
 * deta hai. Token URL me bhejne ki zaroorat hi nahi padti.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [googleEnabled, setGoogleEnabled] = useState(false)
  // AI search box dikhana hai ya nahi — server batata hai (key hai ya nahi).
  // Frontend khud nahi jaan sakta, aur jaanna bhi nahi chahiye.
  const [aiSearchEnabled, setAiSearchEnabled] = useState(false)

  // Silent refresh ka timer
  const refreshTimer = useRef(null)

  /**
   * Access token expire hone se 1 min pehle chupchap naya le lo.
   *
   * Bina iske user 30 min baad beech kaam me 401 khata — booking karte
   * waqt. api.js ka retry usse bacha leta hai, par ye usse pehle hi
   * problem khatam kar deta hai.
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
        setUser(null)      // refresh token bhi mar gaya -> login page
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

  // Mount pe: Google config lo aur session restore karne ki koshish karo
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
        /* backend down hai — health card error dikha dega */
      }

      try {
        const data = await api.refreshSession()
        if (data && !cancelled) applySession(data)
      } catch {
        /* koi valid cookie nahi — normal hai, login page dikhega */
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

  // Google callback ke baad URL saaf kar do (?auth=google / ?auth_error=...)
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
        /* server pe fail bhi ho jaye to client side logout to karna hi hai */
      }
      clearTimeout(refreshTimer.current)
      api.setAccessToken(null)
      setUser(null)
    },

    googleLogin() {
      // Full page redirect — SPA navigation nahi. Backend Google pe bhejega.
      window.location.href = api.googleLoginUrl()
    },
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
