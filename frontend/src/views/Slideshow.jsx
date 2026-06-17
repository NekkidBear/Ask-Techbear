import { useState, useEffect } from 'react'
import axios from 'axios'

const DISPLAY_DURATION = 15000 // 15 seconds per card

export default function Slideshow() {
  const [highlights, setHighlights] = useState([])
  const [index, setIndex] = useState(0)
  const [loading, setLoading] = useState(true)

  // Fetch highlighted questions, refresh periodically
  useEffect(() => {
    const fetchHighlights = async () => {
      try {
        const res = await axios.get('/api/questions/highlighted')
        setHighlights(res.data)
      } catch (err) {
        console.error('Failed to fetch highlights', err)
      } finally {
        setLoading(false)
      }
    }

    fetchHighlights()
    const refreshInterval = setInterval(fetchHighlights, 30000)
    return () => clearInterval(refreshInterval)
  }, [])

  // Auto-cycle through highlights
  useEffect(() => {
    if (highlights.length === 0) return
    const cycleInterval = setInterval(() => {
      setIndex(prev => (prev + 1) % highlights.length)
    }, DISPLAY_DURATION)
    return () => clearInterval(cycleInterval)
  }, [highlights])

  const current = highlights[index]

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
          <div className="w-16 h-16 rounded-full bg-cyan-500 flex items-center justify-center text-3xl flex-shrink-0">
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
            {current.llm_draft}
          </p>
        </div>

        {/* Progress dots */}
        {highlights.length > 1 && (
          <div className="flex justify-center gap-2 mt-8">
            {highlights.map((_, i) => (
              <div
                key={i}
                className={`w-2 h-2 rounded-full transition-colors
                  ${i === index ? 'bg-cyan-400' : 'bg-gray-700'}`}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}