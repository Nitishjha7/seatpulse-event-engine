import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { useBooking } from '../booking/BookingContext'
import BookingConfirmedModal from '../components/BookingConfirmedModal'
import BookingsList from '../components/BookingsList'
import EventHero from '../components/EventHero'
import EventSummary from '../components/EventSummary'
import FeatureStrip from '../components/FeatureStrip'
import HoldCard from '../components/HoldCard'
import PricingBanner from '../components/PricingBanner'
import SeatGrid from '../components/SeatGrid'
import SeatSearch from '../components/SeatSearch'

export default function Dashboard() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [groupSize, setGroupSize] = useState(3)
  const {
    event,
    seats,
    counts,
    pricing,
    bookings,
    selectedSeat,
    lockSecondsLeft,
    lastBooking,
    locking,
    booking,
    message,
    selectSeat,
    releaseHold,
    payForSeat,
    startGroup,
    cancel,
    dismissLastBooking,
  } = useBooking()

  return (
    <div className="animate-rise space-y-5">
      {/* Right rail moves below the grid on screens smaller than xl */}
      <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
        <div className="space-y-5">
          <EventHero event={event} totalSeats={seats.length} />
          <SeatGrid
            seats={seats}
            selectedSeat={selectedSeat}
            onSelect={selectSeat}
            currentUserId={user.id}
            busy={locking}
            layout={event?.layout}
          />
        </div>

        <div className="space-y-5">
          {/* AI search component handles its own conditional rendering */}
          <SeatSearch
            eventId={event?.id}
            onPick={(match) => {
              // Select the first seat to highlight matches in the grid
              const seat = seats.find((s) => s.id === match.seat_ids[0])
              if (seat) selectSeat(seat)
            }}
          />

          <EventSummary event={event} counts={counts} />
          {/* PricingBanner placed above HoldCard to provide context for the 'price locked' badge */}
          <PricingBanner pricing={pricing} />
          <HoldCard
            seat={selectedSeat}
            secondsLeft={lockSecondsLeft}
            onPay={payForSeat}
            onRelease={releaseHold}
            booking={booking}
            message={message}
            groupSize={groupSize}
            onGroupSizeChange={setGroupSize}
            onStartGroup={async () => {
              const token = await startGroup(groupSize)
              // Redirect to share page upon successful token generation
              if (token) navigate(`/groups/${token}`)
            }}
          />
          <BookingsList bookings={bookings} onCancel={cancel} compact limit={3} />
        </div>
      </div>

      <FeatureStrip />

      {lastBooking && (
        <BookingConfirmedModal
          booking={lastBooking.booking}
          seat={lastBooking.seat}
          event={lastBooking.event}
          onClose={dismissLastBooking}
        />
      )}
    </div>
  )
}
