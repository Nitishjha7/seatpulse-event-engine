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
 * Role-gated route — UX only. Real enforcement is backend `require_role`,
 * since client checks are trivially bypassed via DevTools.
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
    // key={user.id} resets state on user switch so sessions don't leak into each other.
    // BookingProvider sits outside Routes so WebSocket/seat state survives navigation.
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
