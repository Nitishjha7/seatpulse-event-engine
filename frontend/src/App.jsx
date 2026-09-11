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
 * ⚠️ This is for UX only; actual security is enforced via backend `require_role`.
 * Client-side checks are easily bypassed (e.g., via React DevTools), so do not
 * rely on this for security. It merely prevents navigation to unauthorized views.
 */
function RequireRole({ roles, children }) {
  const { user } = useAuth()
  return roles.includes(user.role) ? children : <Navigate to="/" replace />
}

export default function App() {
  const { loading, isAuthenticated, user } = useAuth()

  // Prevent UI flicker during session restoration.
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-500">
        Loading…
      </div>
    )
  }

  if (!isAuthenticated) return <AuthPage />

  return (
    // key={user.id} ensures state resets on user switch, preventing data leakage
    // between sessions.
    //
    // BookingProvider is outside Routes to persist WebSocket and seat state
    // across navigation, avoiding unnecessary reconnections.
    <BookingProvider key={user.id}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="events" element={<Events />} />
          <Route path="events/:id" element={<EventDetail />} />
          <Route path="bookings" element={<MyBookings />} />
          <Route path="profile" element={<Profile />} />
          <Route path="pay/:paymentId" element={<MockCheckout />} />
          {/* Entry point for shared group links */}
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
