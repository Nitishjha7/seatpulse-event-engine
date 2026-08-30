import { useAuth } from '../auth/AuthContext'
import { useBooking } from '../booking/BookingContext'
import BookingConfirmedModal from '../components/BookingConfirmedModal'
import BookingsList from '../components/BookingsList'
import EventHero from '../components/EventHero'
import EventSummary from '../components/EventSummary'
import FeatureStrip from '../components/FeatureStrip'
import HoldCard from '../components/HoldCard'
import SeatGrid from '../components/SeatGrid'

export default function Dashboard() {
  const { user } = useAuth()
  const {
    event,
    seats,
    counts,
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
    cancel,
    dismissLastBooking,
  } = useBooking()

  return (
    <div className="animate-rise space-y-5">
      {/* xl se neeche right rail grid ke neeche chala jata hai */}
      <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
        <div className="space-y-5">
          <EventHero event={event} totalSeats={seats.length} />
          <SeatGrid
            seats={seats}
            selectedSeat={selectedSeat}
            onSelect={selectSeat}
            currentUserId={user.id}
            busy={locking}
          />
        </div>

        <div className="space-y-5">
          <EventSummary event={event} counts={counts} />
          <HoldCard
            seat={selectedSeat}
            secondsLeft={lockSecondsLeft}
            onPay={payForSeat}
            onRelease={releaseHold}
            booking={booking}
            message={message}
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
