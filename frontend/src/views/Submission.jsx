import { useState } from 'react'
import axios from 'axios'

// Placeholder avatar — replace with actual TechBear image later
const TECHBEAR_AVATAR = '/techbear-avatar.png'

export default function Submission() {
  const [name, setName] = useState('')
  const [question, setQuestion] = useState('')
  const [status, setStatus] = useState('idle')
  // idle | submitting | success | error

  const handleSubmit = async () => {
    if (!name.trim() || !question.trim()) return

    setStatus('submitting')
    try {
      await axios.post('/api/questions', {
        attendee_name: name.trim(),
        question_text: question.trim(),
      })
      setStatus('success')
    } catch (err) {
      console.error(err)
      setStatus('error')
    }
  }

  // ── Success screen ──────────────────────────────────────────
  if (status === 'success') {
    return (
      <div className="min-h-screen bg-gray-900 flex flex-col items-center justify-center px-6">
        <div className="w-20 h-20 rounded-full bg-cyan-500 flex items-center justify-center text-4xl mb-6">
          🐻
        </div>
        <h2 className="text-white text-2xl font-bold mb-3 text-center">
          TechBear got your question, sugar!
        </h2>
        <p className="text-gray-400 text-center max-w-sm">
          Sit tight, darling. Your question is in the queue and
          TechBear will get to it shortly. 🐾
        </p>
      </div>
    )
  }

  // ── Main form ───────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-900 flex flex-col">

      {/* Header — messenger-style contact bar */}
      <div className="bg-gray-800 border-b border-gray-700 px-4 py-3 flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-cyan-500 flex items-center justify-center text-xl">
          🐻
        </div>
        <div>
          <p className="text-white font-semibold text-sm">TechBear</p>
          <p className="text-green-400 text-xs">● Online and ready to help</p>
        </div>
        <div className="ml-auto">
          <span className="text-xs text-gray-400">Gymnarctos Studios</span>
        </div>
      </div>

      {/* Chat area — shows a greeting bubble */}
      <div className="flex-1 px-4 py-6 flex flex-col gap-4">
        <div className="flex items-start gap-3 max-w-xs">
          <div className="w-8 h-8 rounded-full bg-cyan-500 flex items-center justify-center text-sm flex-shrink-0">
            🐻
          </div>
          <div className="bg-gray-700 rounded-2xl rounded-tl-none px-4 py-3">
            <p className="text-white text-sm">
              Well hello there, sugar! 👋 Got a tech question?
              Drop it below and TechBear will handle it with
              all the sass and wisdom you deserve. 🐾
            </p>
          </div>
        </div>
      </div>

      {/* Input area */}
      <div className="bg-gray-800 border-t border-gray-700 px-4 py-4 flex flex-col gap-3">

        {/* Name field */}
        <input
          type="text"
          placeholder="Your name, darling..."
          value={name}
          onChange={e => setName(e.target.value)}
          maxLength={100}
          className="w-full bg-gray-700 text-white placeholder-gray-400
                     rounded-full px-4 py-2 text-sm outline-none
                     focus:ring-2 focus:ring-cyan-500"
        />

        {/* Question field */}
        <div className="flex items-end gap-2">
          <textarea
            placeholder="Ask TechBear anything about tech..."
            value={question}
            onChange={e => setQuestion(e.target.value)}
            maxLength={500}
            rows={3}
            className="flex-1 bg-gray-700 text-white placeholder-gray-400
                       rounded-2xl px-4 py-2 text-sm outline-none resize-none
                       focus:ring-2 focus:ring-cyan-500"
          />
          <button
            onClick={handleSubmit}
            disabled={
              status === 'submitting' ||
              !name.trim() ||
              !question.trim()
            }
            className="bg-cyan-500 hover:bg-cyan-400 disabled:bg-gray-600
                       disabled:cursor-not-allowed text-white rounded-full
                       w-10 h-10 flex items-center justify-center
                       text-lg flex-shrink-0 transition-colors"
          >
            {status === 'submitting' ? '⏳' : '🐾'}
          </button>
        </div>

        {/* Character count */}
        <p className="text-gray-500 text-xs text-right">
          {question.length}/500
        </p>

        {/* Error state */}
        {status === 'error' && (
          <p className="text-red-400 text-xs text-center">
            Honey, something went wrong. Try again in a moment!
          </p>
        )}
      </div>
    </div>
  )
}