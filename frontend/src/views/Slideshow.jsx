import { useState, useEffect, useCallback, useRef } from 'react'
import axios from 'axios'
// Import the custom avatar component safely!
import TechbearAvatar from '../components/Techbearavatar'

const DISPLAY_DURATION = 15000 // 15 seconds per card

export default function Slideshow() {
  const [highlights, setHighlights] = useState([])
  const [index, setIndex] = useState(0)
  const [loading, setLoading] = useState(true)

  // Use a ref for the interval so startTimer can always close over
  // the current highlights length without stale state issues.
  const intervalRef = useRef(null)
  const countRef = useRef(0)

  const startTimer = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (countRef.current === 0) return
    intervalRef.current = setInterval(() => {
      setIndex(prev => (prev + 1) % countRef.current)
    }, DISPLAY_DURATION)
  }, [])

  // Fetch highlighted questions, refresh periodically
  useEffect(() => {
    const fetchHighlights = async () => {
      try {
        const res = await axios.get('/api/questions/highlighted')
        setHighlights(res.data)
        countRef.current = res.data.length
        startTimer()
      } catch (err) {
        console.error('Failed to fetch highlights', err)
      } finally {
        setLoading(false)
      }
    }

    fetchHighlights()
    const refreshInterval = setInterval(fetchHighlights, 30000)
    return () => {
      clearInterval(refreshInterval)
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [startTimer])

  // Manual navigation — resets the auto-advance timer
  const goNext = useCallback(() => {
    if (countRef.current === 0) return
    setIndex(prev => (prev + 1) % countRef.current)
    startTimer()
  }, [startTimer])

  const goPrev = useCallback(() => {
    if (countRef.current === 0) return
    setIndex(prev => (prev - 1 + countRef.current) % countRef.current)
    startTimer()
  }, [startTimer])

  // Keyboard navigation (arrow keys)
  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') goNext()
      if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp'  ) goPrev()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [goNext, goPrev])

  const current = highlights[index]

  // Prefer the formatted presentation_text; fall back to raw llm_draft
  // so nothing goes blank if a presentation version hasn't been seeded yet.
  const displayText = current?.presentation_text ?? current?.llm_draft

  // ── Holding screen — no highlights yet ──
  if (!loading && highlights.length === 0) {
    return (
      <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center">
        {/* Render big avatar on holding page 🎉 */}
        <div className="mb-6 animate-pulse">
          <TechbearAvatar size="lg" border={true} />
        </div>
        <h1 className="text-white text-3xl font-bold mb-2">Ask TechBear</h1>
        <p className="text-gray-400 text-lg">Got a tech question? Scan the QR code below!</p>
      </div>
    )
  }

  if (loading || !current) {
    return <div className="min-h-screen bg-gray-900" />
  }

  // ── Main slideshow card ──
  return (
    <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center px-16">
      <div
        key={current.id}
        className="max-w-4xl w-full animate-[fadeIn_0.6s_ease-in-out]"
      >
        {/* Header */}
        <div className="flex items-center gap-4 mb