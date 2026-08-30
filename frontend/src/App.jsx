import { Navigate, Route, Routes } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import AuthPage from './auth/AuthPage'
import { BookingProvider } from './booking/BookingContext'
import AppShell from './layout/AppShell'
import AdminStats from './pages/admin/AdminStats'
import Dashboard from './pages/Dashboard'
import EventDetail from './pages/EventDetail'
import Events from './pages/Events'
import GroupBooking from './pages/GroupBooking'
import MockCheckout from './pages/MockCheckout'
import MyBookings from './pages/MyBookings'
import PaymentReturn from './pages/PaymentReturn'
import GatePortal from './pages/gate/GatePortal'
import CreateEvent from './pages/organizer/CreateEvent'
import MyEvents from './pages/organizer/MyEvents'
import Profile from './pages/Profile'

/**
 * Role-gated route.
 *
 * ⚠️ Ye sirf UX ke liye hai — asli security backend me hai (`require_role`).
 * Frontend check bypass karna trivial hai (React DevTools se state badal do),
 * isliye client-side gate ko kabhi security mat maanna. Ye bas user ko wo
 * page dikhne se rokta hai jo waise bhi 403 dega.
 */
function RequireRole({ roles, children }) {
  const { user } = useAuth()
  return roles.includes(user.role) ? children : <Navigate to="/" replace />
}

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
          <Route path="pay/:paymentId" element={<MockCheckout />} />
          {/* Group link se aane wala seedha yahan land karta hai */}
          <Route path="groups/:shareToken" element={<GroupBooking />} />
          <Route path="payment/return" element={<PaymentReturn />} />

          <Route
            path="organizer/events"
            element={
              <RequireRole roles={['organizer', 'admin']}>
                <MyEvents />
              </RequireRole>
            }
          />
          <Route
            path="organizer/events/new"
            element={
              <RequireRole roles={['organizer', 'admin']}>
                <CreateEvent />
              </RequireRole>
            }
          />
          <Route
            path="gate"
            element={
              <RequireRole roles={['organizer', 'admin']}>
                <GatePortal />
              </RequireRole>
            }
          />
          <Route
            path="admin"
            element={
              <RequireRole roles={['admin']}>
                <AdminStats />
              </RequireRole>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BookingProvider>
  )
}
