import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'
// Import the custom avatar component safely!
import TechbearAvatar from '../components/Techbearavatar'

export default function Dashboard() {
  const [questions, setQuestions] = useState([])
  const [selected, setSelected] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('pending')
  const [generating, setGenerating] = useState(false)

  const fetchQuestions = useCallback(async () => {
    try {
      const params = filter !== 'all' ? { status: filter } : {}
      const res = await axios.get('/api/questions/', { params })
      setQuestions(res.data)
    } catch (err) {
      console.error('Failed to fetch questions', err)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    fetchQuestions()
    // Poll every 5 seconds for new submissions
    const interval = setInterval(fetchQuestions, 5000)
    return () => clearInterval(interval)
  }, [fetchQuestions])

  const updateQuestion = async (id, updates) => {
    try {
      await axios.patch(`/api/questions/${id}`, updates)
      await fetchQuestions()
      if (selected?.id === id) {
        setSelected(prev => ({ ...prev, ...updates }))
      }
    } catch (err) {
      console.error('Failed to update question', err)
    }
  }

  const generateDraft = async (id) => {
    setGenerating(true)
    try {
      const res = await axios.post(`/api/questions/${id}/generate`)
      setSelected(res.data)
      await fetchQuestions()
    } catch (err) {
      console.error('Failed to generate draft', err)
    } finally {
      setGenerating(false)
    }
  }

  const statusColor = {
    pending: 'bg-yellow-500',
    approved: 'bg-blue-500',
    rejected: 'bg-red-500',
    answered: 'bg-green-500',
    highlighted: 'bg-purple-500',
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white flex">

      {/* ── Sidebar: Queue ── */}
      <div className="w-96 border-r border-gray-700 flex flex-col">
        <div className="p-4 border-b border-gray-700">
          <h1 className="text-xl font-bold flex items-center gap-3">
            {/* Replaced placeholder emoji with small custom avatar */}
            <TechbearAvatar size="sm" border={false} />
            <span>TechBear Dashboard</span>
          </h1>
          <div className="flex gap-2 mt-3">
            {['pending', 'approved', 'all'].map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`text-xs px-3 py-1 rounded-full capitalize transition-colors
                  ${filter === f
                    ? 'bg-cyan-500 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading && (
            <p className="text-gray-500 text-sm p-4">Loading queue...</p>
          )}

          {!loading && questions.length === 0 && (
            <p className="text-gray-500 text-sm p-4">
              No questions in the queue yet.
            </p>
          )}

          {questions.map(q => (
            <button
              key={q.id}
              onClick={() => setSelected(q)}
              className={`w-full text-left p-4 border-b border-gray-800
                hover:bg-gray-800 transition-colors
                ${selected?.id === q.id ? 'bg-gray-800' : ''}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-sm">{q.attendee_name}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded-full ${statusColor[q.status] || 'bg-gray-600'}`}>
                  {q.status}
                </span>
              </div>
              <p className="text-gray-400 text-xs line-clamp-2">
                {q.question_text}
              </p>
            </button>
          ))}
        </div>
      </div>

      {/* ── Main panel: Selected question + draft ── */}
      <div className="flex-1 flex flex-col">
        {!selected && (
          <div className="flex-1 flex items-center justify-center text-gray-500">
            Select a question from the queue to begin
          </div>
        )}

        {selected && (
          <div className="flex-1 flex flex-col p-6 gap-6 overflow-y-auto">

            {/* Question detail */}
            <div className="bg-gray-800 rounded-xl p-5">
              <div className="flex items-center justify-between mb-2">
                <h2 className="font-bold text-lg">{selected.attendee_name}</h2>
                <span className={`text-xs px-3 py-1 rounded-full ${statusColor[selected.status] || 'bg-gray-600'}`}>
                  {selected.status}
                </span>
              </div>
              <p className="text-gray-200">{selected.question_text}</p>
            </div>

            {/* Draft panel */}
            <div className="bg-gray-800 rounded-xl p-5 flex-1">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  {/* Replaced placeholder emoji with small custom avatar */}
                  <TechbearAvatar size="sm" border={false} />
                  <h3 className="text-sm font-semibold text-gray-400">
                    TechBear's Draft Response
                  </h3>
                </div>
                <button
                  onClick={() => generateDraft(selected.id)}
                  disabled={generating}
                  className="bg-cyan-600 hover:bg-cyan-500 disabled:bg-gray-600
                             disabled:cursor-not-allowed text-xs px-3 py-1.5
                             rounded-full font-semibold transition-colors"
                >
                  {generating ? '🐾 Drafting...' : '🐾 Generate Draft'}
                </button>
              </div>
              {selected.llm_draft ? (
                <p className="text-gray-100 whitespace-pre-wrap">
                  {selected.llm_draft}
                </p>
              ) : (
                <p className="text-gray-500 italic">
                  No draft yet. Click "Generate Draft" to have TechBear take a crack at it.
                </p>
              )}
            </div>

            {/* Controls */}
            <div className="flex gap-3">
              <button
                onClick={() => updateQuestion(selected.id, { status: 'approved' })}
                className="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded-lg text-sm font-semibold"
              >
                Approve
              </button>
              <button
                onClick={() => updateQuestion(selected.id, { status: 'rejected' })}
                className="bg-red-600 hover:bg-red-500 px-4 py-2 rounded-lg text-sm font-semibold"
              >
                Reject
              </button>
              <button
                onClick={() => updateQuestion(selected.id, { status: 'answered', answered_at: new Date().toISOString() })}
                className="bg-green-600 hover:bg-green-500 px-4 py-2 rounded-lg text-sm font-semibold"
              >
                Mark Answered
              </button>
              <button
                onClick={() => updateQuestion(selected.id, { highlight: !selected.highlight })}
                className={`px-4 py-2 rounded-lg text-sm font-semibold
                  ${selected.highlight
                    ? 'bg-purple-600 hover:bg-purple-500'
                    : 'bg-gray-700 hover:bg-gray-600'}`}
              >
                {selected.highlight ? '★ Highlighted' : '☆ Highlight'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}