import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'

import {
  API_URL,
  cancelBooking,
  createBooking,
  getAccessToken,
  startCheckout,
  getEvent,
  getEventSeats,
  getEvents,
  getHealth,
  getMyBookings,
  lockSeat,
  unlockSeat,
} from '../api'
import { useAuth } from '../auth/AuthContext'
import { useWebSocket } from '../hooks/useWebSocket'

const BookingContext = createContext(null)

/**
 * Ek seat par abhi kya price lagega.
 *
 * Priority saaf hai aur JAAN-BOOJH ke isi order me hai:
 *   1. held_price   — hold ke waqt lock hua quote. Sabse upar, kyunki
 *                     user se yahi waada kiya gaya hai.
 *   2. current_price — server ka calculated dynamic price
 *   3. price         — base (dynamic pricing off ho, ya purana API response)
 *
 * Ye ek hi function hai jo ye faisla karta hai. Har component apna hisaab
 * lagata to kisi ek jagah `held_price` bhoolna aasan hota — aur wahi ek
 * jagah user ko galat price dikha deti.
 */
export function seatPrice(seat) {
  if (!seat) return null
  return seat.held_price ?? seat.current_price ?? seat.price
}

/** Error ko user ke padhne layak text me badlo. */
function errorText(err) {
  if (err.status === 429) {
    // Server Retry-After header me batata hai kitni der ruko
    const wait = err.retryAfter ? ` ${err.retryAfter} second ruk ke try karo.` : ''
    return `🐢 Thoda dheere!${wait}`
  }
  if (err.status === 409) return `⚠️ ${err.message}`
  return err.message
}

export function useBooking() {
  const ctx = useContext(BookingContext)
  if (!ctx) throw new Error('useBooking ko BookingProvider ke andar hi use karo')
  return ctx
}

/**
 * Poora booking state ek jagah.
 *
 * Pehle ye sab App.jsx me tha. Ab multiple pages (Dashboard, My Bookings,
 * Events) ko same data chahiye — aur sabse important, **WebSocket connection
 * ek hi rehna chahiye**. Har page apna socket kholta to server pe 4 gunа
 * connections ban jaate aur updates duplicate aate.
 */
export function BookingProvider({ children }) {
  const { user } = useAuth()

  const [health, setHealth] = useState(null)
  const [events, setEvents] = useState([])
  const [event, setEvent] = useState(null)
  const [seats, setSeats] = useState([])
  const [bookings, setBookings] = useState([])
  // Event ka demand/surge state. WebSocket se live update hota hai,
  // isliye `event.pricing` se alag rakha hai — warna har pricing message
  // pe poora event object replace karna padta.
  const [pricing, setPricing] = useState(null)

  const [selectedSeat, setSelectedSeat] = useState(null)
  const [lockSecondsLeft, setLockSecondsLeft] = useState(0)
  // Booking confirm hone ke baad success modal ka data: { booking, seat, event }
  const [lastBooking, setLastBooking] = useState(null)

  const [locking, setLocking] = useState(false)
  const [booking, setBooking] = useState(false)
  const [message, setMessage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fatalError, setFatalError] = useState(null)

  // Cleanup me latest value chahiye, par uspe effect dobara nahi chalana
  const selectedRef = useRef(null)
  selectedRef.current = selectedSeat

  // Pricing update aane par kaunsa event refetch karna hai — callback ko
  // `event` pe depend karana pada to har event change pe socket handler
  // badal jata (aur wo useWebSocket ke andar reconnect trigger karta).
  const eventIdRef = useRef(null)
  const pricingRefetchRef = useRef(null)

  const refresh = useCallback(async (eventId) => {
    const [eventData, seatData, bookingData] = await Promise.all([
      getEvent(eventId),
      getEventSeats(eventId),
      getMyBookings(),
    ])
    setEvent(eventData)
    setSeats(seatData)
    setBookings(bookingData)
    setPricing(eventData.pricing ?? null)
    eventIdRef.current = eventId
  }, [])

  useEffect(() => {
    async function init() {
      try {
        const [healthData, eventList] = await Promise.all([getHealth(), getEvents()])
        setHealth(healthData)
        setEvents(eventList)

        if (eventList.length === 0) {
          setFatalError(
            "Koi event nahi mila — 'docker compose exec backend python seed.py' chalao",
          )
          return
        }
        await refresh(eventList[0].id)
      } catch (err) {
        setFatalError(err.message)
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [refresh])

  /** WebSocket se live seat updates — sirf badli hui seat replace hoti hai. */
  const handleSeatUpdate = useCallback(
    (updatedSeat) => {
      setSeats((prev) => prev.map((s) => (s.id === updatedSeat.id ? updatedSeat : s)))

      // Meri hold kisi aur ke paas chali gayi? Selection saaf karo
      setSelectedSeat((prev) => {
        if (!prev || prev.id !== updatedSeat.id) return prev
        const stillMine =
          updatedSeat.status === 'locked' && updatedSeat.locked_by === user.id
        if (stillMine) return updatedSeat
        setLockSecondsLeft(0)
        return null
      })
    },
    [user.id],
  )

  /**
   * Demand se price badla — poore event ka multiplier aaya hai.
   *
   * ⚠️ Yahan hum client-side me `base × multiplier` NAHI karte, chahe wo
   * ek line ka kaam ho. Wajah: JS ka Math.round(100.5) = 101, par Python ka
   * round(100.5) = 100. Ties par dono alag jawab dete hain — matlab UI
   * ₹1010 dikhata aur server ₹1000 charge karta. ₹10 ka farq chhota lagta
   * hai, par "jo dikha wahi kata" ka bharosa toot jata hai.
   *
   * Isliye: banner turant update karo (wahi user dekhta hai), aur exact
   * prices server se hi lo.
   */
  const handlePricingUpdate = useCallback((next) => {
    setPricing(next)

    // Debounce — flash sale me ek second me 20 bookings ho sakti hain,
    // aur har ek pe seat refetch karna server ko bekaar me peetna hai.
    clearTimeout(pricingRefetchRef.current)
    pricingRefetchRef.current = setTimeout(() => {
      const id = eventIdRef.current
      if (id) getEventSeats(id).then(setSeats).catch(() => {})
    }, 400)
  }, [])

  const { status: wsStatus } = useWebSocket(
    event?.id ?? null,
    handleSeatUpdate,
    handlePricingUpdate,
  )

  // Unmount pe pending refetch cancel — warna gaye hue page ka setState
  useEffect(() => () => clearTimeout(pricingRefetchRef.current), [])

  /**
   * Hold ka countdown — sirf DIKHANE ke liye.
   * Asli expiry Redis TTL me hoti hai: browser band kar do ya tab crash ho
   * jaye, seat phir bhi 5 min me apne aap free ho jayegi.
   */
  useEffect(() => {
    if (lockSecondsLeft <= 0) return

    const id = setInterval(() => {
      setLockSecondsLeft((s) => {
        if (s <= 1) {
          setSelectedSeat(null)
          setMessage({
            type: 'error',
            text: '⏱️ Hold time khatam — seat wapas available hai',
          })
          if (event) refresh(event.id)
          return 0
        }
        return s - 1
      })
    }, 1000)

    return () => clearInterval(id)
  }, [lockSecondsLeft, event, refresh])

  // Tab band karte waqt lock chhod do — TTL ka wait na karna pade
  useEffect(() => {
    const handler = () => {
      const seat = selectedRef.current
      const token = getAccessToken()
      if (seat && token) {
        fetch(`${API_URL}/api/seats/${seat.id}/lock`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
          keepalive: true, // page band hote waqt bhi request nikal jati hai
        })
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  async function selectSeat(seat) {
    if (locking) return

    // Wahi seat dubara click = deselect + lock release
    if (selectedSeat?.id === seat.id) {
      await releaseHold()
      return
    }

    setLocking(true)
    setMessage(null)
    try {
      // Purani seat ka lock pehle chhodo, warna do seats hold rahengi
      if (selectedSeat) await unlockSeat(selectedSeat.id).catch(() => {})

      const lock = await lockSeat(seat.id)
      // Server ne is hold ke liye jo price LOCK kiya, wahi seat pe chipka
      // dete hain. Checkout card seedha isse dikhata hai — dobara calculate
      // karne ki koshish hi nahi karta, kyunki charge exactly yahi hoga.
      setSelectedSeat({ ...seat, held_price: lock.price })
      setLockSecondsLeft(lock.expires_in)
      setMessage({
        type: 'success',
        text: `Seat ${seat.row_label}-${seat.seat_number} hold ho gayi`,
      })
    } catch (err) {
      setSelectedSeat(null)
      setLockSecondsLeft(0)
      setMessage({ type: 'error', text: errorText(err) })
    } finally {
      setLocking(false)
      await refresh(event.id)
    }
  }

  async function releaseHold() {
    if (!selectedSeat) return
    try {
      await unlockSeat(selectedSeat.id)
    } catch {
      /* lock TTL pe expire ho chuka hoga — koi baat nahi */
    }
    setSelectedSeat(null)
    setLockSecondsLeft(0)
    setMessage(null)
    await refresh(event.id)
  }

  /**
   * Checkout shuru karo — user ko gateway pe bhejo.
   *
   * ⚠️ Yahan booking NAHI banti. Booking tabhi banti hai jab payment
   * confirm ho — aur wo confirmation webhook se aati hai, is redirect se
   * nahi. Ye function sirf session bana ke user ko bhej deta hai.
   */
  async function payForSeat() {
    if (!selectedSeat) return

    setBooking(true)
    setMessage(null)
    try {
      const session = await startCheckout(selectedSeat.id)
      // Full page redirect, SPA navigation nahi — Stripe ke case me ye
      // unka domain hota hai, mock me hamara apna /pay/:id page.
      window.location.href = session.checkout_url
    } catch (err) {
      setMessage({ type: 'error', text: errorText(err) })
      setBooking(false)
      await refresh(event.id)
    }
  }

  async function confirmBooking() {
    if (!selectedSeat) return

    setBooking(true)
    setMessage(null)
    try {
      const created = await createBooking(selectedSeat.id)

      // Success modal ke liye. Seat aur event abhi capture kar rahe hain,
      // kyunki neeche selectedSeat null ho jayega aur refresh ke baad wo
      // seat 'booked' ho chuki hogi.
      setLastBooking({ booking: created, seat: selectedSeat, event })

      setSelectedSeat(null)
      setLockSecondsLeft(0)
    } catch (err) {
      setMessage({ type: 'error', text: errorText(err) })
    } finally {
      setBooking(false)
      await refresh(event.id)
    }
  }

  async function cancel(bookingId) {
    setMessage(null)
    try {
      await cancelBooking(bookingId)
      setMessage({ type: 'success', text: 'Booking cancel — seat wapas available hai' })
      await refresh(event.id)
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    }
  }

  // Counts hamesha seats se derive — WebSocket update aate hi apne aap sahi.
  // Server se dobara poochne ki zaroorat nahi.
  const counts = seats.reduce(
    (acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }),
    {},
  )

  const value = {
    health,
    wsStatus,
    events,
    event,
    seats,
    counts,
    pricing,
    seatPrice,
    bookings,
    selectedSeat,
    lockSecondsLeft,
    lastBooking,
    locking,
    booking,
    message,
    loading,
    fatalError,
    selectSeat,
    releaseHold,
    confirmBooking,
    payForSeat,
    cancel,
    dismissLastBooking: () => setLastBooking(null),
    clearMessage: () => setMessage(null),
  }

  return <BookingContext.Provider value={value}>{children}</BookingContext.Provider>
}
