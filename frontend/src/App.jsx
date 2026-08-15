import { Navigate, Route, Routes } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import AuthPage from './auth/AuthPage'
import { BookingProvider } from './booking/BookingContext'
import AppShell from './layout/AppShell'
import Dashboard from './pages/Dashboard'
import EventDetail from './pages/EventDetail'
import Events from './pages/Events'
import MyBookings from './pages/MyBookings'
import Profile from './pages/Profile'

export default function App() {
  const { loading, isAuthenticated, user } = useAuth()

  // Session restore hone tak kuch mat dikhao — warna ek pal ko login page
  // flash hota hai aur phir gayab ho jata hai
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-500">
        Loading…
      </div>
    )
  }

  if (!isAuthenticated) return <AuthPage />

  return (
    // key={user.id} — user badalne par poora state fresh. Warna pichhle user
    // ki bookings aur selection nayi login me dikh jaati.
    //
    // BookingProvider Routes ke BAHAR hai, isliye page badalne par WebSocket
    // aur seat state bache rehte hain — har navigation pe reconnect nahi hota.
    <BookingProvider key={user.id}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="events" element={<Events />} />
          <Route path="events/:id" element={<EventDetail />} />
          <Route path="bookings" element={<MyBookings />} />
          <Route path="profile" element={<Profile />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BookingProvider>
  )
}
