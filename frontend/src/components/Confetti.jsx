import { useMemo } from 'react'

const COLORS = ['#a78bfa', '#34d399', '#fbbf24', '#f472b6', '#60a5fa', '#f87171']

/**
 * CSS-only confetti.
 *
 * Koi library nahi (canvas-confetti ~30KB hai). 40 chhote divs hain, har ek
 * ka apna random direction, rotation, color aur delay — CSS variables ke
 * through animation me jaate hain.
 *
 * `useMemo` zaroori hai: bina iske har render pe naye random numbers
 * bante aur confetti jhatke se jagah badalta rehta.
 */
export default function Confetti({ count = 40 }) {
  const pieces = useMemo(
    () =>
      Array.from({ length: count }, () => ({
        left: Math.random() * 100,
        // Do taraf bikhare — kabhi left, kabhi right
        x: `${(Math.random() - 0.5) * 220}px`,
        r: `${Math.random() * 720 - 360}deg`,
        delay: Math.random() * 0.5,
        duration: 1.2 + Math.random() * 0.9,
        color: COLORS[Math.floor(Math.random() * COLORS.length)],
        size: 5 + Math.random() * 5,
        round: Math.random() > 0.6,
      })),
    [count],
  )

  return (
    // pointer-events-none — confetti ke upar click karne pe button dabna chahiye
    <div className="pointer-events-none absolute inset-x-0 top-0 h-full overflow-hidden" aria-hidden="true">
      {pieces.map((p, i) => (
        <span
          key={i}
          className="animate-confetti absolute top-0"
          style={{
            left: `${p.left}%`,
            width: p.size,
            height: p.size * (p.round ? 1 : 1.6),
            background: p.color,
            borderRadius: p.round ? '50%' : '2px',
            animationDelay: `${p.delay}s`,
            animationDuration: `${p.duration}s`,
            '--x': p.x,
            '--r': p.r,
          }}
        />
      ))}
    </div>
  )
}
