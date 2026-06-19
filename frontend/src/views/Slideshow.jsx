import { useState, useEffect, useCallback, useRef } from 'react'
import axios from 'axios'

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
        <div className="w-24 h-24 rounded-full bg-cyan-500 flex items-center justify-center text-5xl mb-6 animate-pulse">
          🐻
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
        <div className="flex items-center gap-4 mb-8">
          <div className="w-16 h-16 rounded-full bg-cyan-500 flex items-center justify-center text-3xl shrink-0">
            🐻
          </div>
          <div>
            <h2 className="text-white text-2xl font-bold">Ask TechBear</h2>
            <p className="text-gray-400 text-sm">Gymnarctos Studios</p>
          </div>
        </div>

        {/* Question */}
        <div className="bg-gray-800 rounded-2xl p-8 mb-6">
          <p className="text-cyan-400 text-sm font-semibold mb-2">
            {current.attendee_name} asked:
          </p>
          <p className="text-white text-2xl leading-relaxed">
            {current.question_text}
          </p>
        </div>

        {/* Answer */}
        <div className="bg-cyan-950 border-2 border-cyan-500 rounded-2xl p-8">
          <p className="text-cyan-300 text-sm font-semibold mb-2">
            TechBear said:
          </p>
          <p className="text-white text-xl leading-relaxed whitespace-pre-wrap">
            {displayText}
          </p>
        </div>

        {/* Navigation controls + progress */}
        <div className="flex items-center justify-between mt-8">

          {/* Prev button */}
          <button
            onClick={goPrev}
            disabled={highlights.length <= 1}
            aria-label="Previous question"
            className="flex items-center gap-2 px-5 py-2.5 rounded-full
                       bg-gray-800 text-gray-300 text-sm font-semibold
                       hover:bg-gray-700 hover:text-white
                       disabled:opacity-30 disabled:cursor-not-allowed
                       transition-colors"
          >
            ← Prev
          </button>

          {/* Progress dots — also clickable */}
          {highlights.length > 1 && (
            <div className="flex gap-2">
              {highlights.map((_, i) => (
                <button
                  key={i}
                  onClick={() => {
                    setIndex(i)
                    startTimer()
                  }}
                  aria-label={`Go to question ${i + 1}`}
                  className={`w-2.5 h-2.5 rounded-full transition-colors
                    ${i === index
                      ? 'bg-cyan-400 scale-125'
                      : 'bg-gray-600 hover:bg-gray-400'}`}
                />
              ))}
            </div>
          )}

          {/* Next button */}
          <button
            onClick={goNext}
            disabled={highlights.length <= 1}
            aria-label="Next question"
            className="flex items-center gap-2 px-5 py-2.5 rounded-full
                       bg-gray-800 text-gray-300 text-sm font-semibold
                       hover:bg-gray-700 hover:text-white
                       disabled:opacity-30 disabled:cursor-not-allowed
                       transition-colors"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  )
}