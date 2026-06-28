/**
 * BatchReview.jsx — Async pipeline review dashboard
 * Ask TechBear — Gymnarctos Studios LLC
 *
 * Layout: two-column
 *   Left  — queue of pipeline runs (filterable by review status)
 *   Right — selected run detail: question, LLM scores, side-by-side
 *           diff (factual draft | voice draft), editable final answer,
 *           flag checkboxes, score sliders, approve / save actions.
 *
 * API surface (all via /api/review/):
 *   GET  /runs               — queue list
 *   GET  /runs/:id           — full run detail
 *   PATCH /runs/:id          — save in progress (no email)
 *   POST  /runs/:id/approve  — commit + optional email
 *   GET  /runs/:id/export    — single-run CSV download
 *   GET  /export             — bulk CSV download
 */

import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import TechbearAvatar from "../components/Techbearavatar";

// =============================================================
// Constants
// =============================================================

const STATUS_FILTERS = ["all", "pending", "in_progress", "complete"];

const FLAG_DEFINITIONS = [
  // Negative flags
  { key: "flag_missed_claim", label: "Missed claim", positive: false },
  {
    key: "flag_unsupported_claim",
    label: "Unsupported claim",
    positive: false,
  },
  { key: "flag_wrong_retrieval", label: "Wrong retrieval", positive: false },
  {
    key: "flag_moderation_false_positive",
    label: "Mod false positive",
    positive: false,
  },
  {
    key: "flag_moderation_false_negative",
    label: "Mod false negative",
    positive: false,
  },
  { key: "flag_too_formulaic", label: "Too formulaic", positive: false },
  { key: "flag_voice_break", label: "Voice break", positive: false },
  { key: "flag_too_salesy", label: "Too salesy", positive: false },
  {
    key: "flag_lore_recall_failure",
    label: "Lore recall failure",
    positive: false,
  },
  {
    key: "flag_verbatim_regurgitation",
    label: "Verbatim regurgitation",
    positive: false,
  },
  // Positive flags
  {
    key: "flag_excellent_response",
    label: "Excellent response",
    positive: true,
  },
  {
    key: "flag_publishable_with_minor_edits",
    label: "Publishable w/ minor edits",
    positive: true,
  },
];

const SCORE_DIMENSIONS = [
  { key: "fact_score", label: "Factual accuracy" },
  { key: "character_score", label: "Character fidelity" },
  { key: "editorial_score", label: "Editorial quality" },
  { key: "semantic_score", label: "Semantic fidelity" },
  { key: "educational_score", label: "Educational value" },
];

const EDIT_EFFORT_LABELS = {
  0: "Publish unchanged",
  1: "Minor edits",
  2: "Moderate edits",
  3: "Major rewrite",
  4: "Reject / unusable",
};

// =============================================================
// Helpers
// =============================================================

/** Derive flag boolean state from the active_flags array in the run */
function flagsFromActiveList(activeFlags = []) {
  const state = {};
  FLAG_DEFINITIONS.forEach(({ key }) => {
    // key is "flag_too_formulaic", note_type is "too_formulaic"
    const noteType = key.replace(/^flag_/, "");
    state[key] = activeFlags.includes(noteType);
  });
  return state;
}

/** Build PATCH body from local edit state — only send changed fields */
function buildPatchBody(scores, flags, meta) {
  const body = {};
  SCORE_DIMENSIONS.forEach(({ key }) => {
    if (scores[key] !== null && scores[key] !== undefined)
      body[key] = scores[key];
  });
  FLAG_DEFINITIONS.forEach(({ key }) => {
    if (flags[key] !== null && flags[key] !== undefined) body[key] = flags[key];
  });
  if (meta.edit_effort !== null && meta.edit_effort !== undefined)
    body.edit_effort = meta.edit_effort;
  if (meta.moderation_correct !== null && meta.moderation_correct !== undefined)
    body.moderation_correct = meta.moderation_correct;
  if (meta.final_answer !== undefined) body.final_answer = meta.final_answer;
  if (meta.review_notes !== undefined) body.review_notes = meta.review_notes;
  return body;
}

// =============================================================
// Sub-components
// =============================================================

/** Single score row: label + LLM score (read-only) + human score slider */
function ScoreRow({ dimension, llmScore, humanScore, onChange }) {
  return (
    <div className="flex items-center gap-3 py-1">
      <span className="text-gray-400 text-xs w-36 shrink-0">
        {dimension.label}
      </span>
      {/* LLM score — read only */}
      <span className="text-xs w-10 text-center font-mono text-cyan-400">
        {llmScore != null ? llmScore.toFixed(1) : "—"}
      </span>
      {/* Human score slider */}
      <input
        type="range"
        min={0}
        max={10}
        step={0.5}
        value={humanScore ?? 0}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="flex-1 accent-amber-400"
      />
      <span className="text-xs w-8 text-right font-mono text-amber-400">
        {humanScore != null ? humanScore.toFixed(1) : "—"}
      </span>
    </div>
  );
}

/** Flag checkbox — color-coded positive vs negative */
function FlagCheckbox({ definition, checked, onChange }) {
  const { key, label, positive } = definition;
  return (
    <label className="flex items-center gap-2 cursor-pointer group">
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange(key, e.target.checked)}
        className={`rounded accent-${positive ? "green" : "red"}-500`}
      />
      <span
        className={`text-xs ${
          checked
            ? positive
              ? "text-green-400"
              : "text-red-400"
            : "text-gray-500 group-hover:text-gray-300"
        }`}
      >
        {label}
      </span>
    </label>
  );
}

/** Markdown-aware textarea — renders preview alongside edit */
function MarkdownEditor({ value, onChange, placeholder, rows = 8 }) {
  const [preview, setPreview] = useState(false);

  // Minimal markdown → HTML for preview (no external dep)
  const renderPreview = (md) => {
    if (!md) return '<p class="text-gray-500 italic">Nothing here yet.</p>';
    let html = md
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(
        /^### (.+)$/gm,
        '<h3 class="text-base font-bold mt-3 mb-1">$1</h3>',
      )
      .replace(/^## (.+)$/gm, '<h2 class="text-lg font-bold mt-4 mb-1">$1</h2>')
      .replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold mt-4 mb-2">$1</h1>')
      .replace(/^---$/gm, '<hr class="border-gray-600 my-3" />')
      .replace(/\n\n/g, '</p><p class="mb-2">');
    return `<p class="mb-2">${html}</p>`;
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-2 justify-end mb-1">
        <button
          onClick={() => setPreview(false)}
          className={`text-xs px-2 py-0.5 rounded ${!preview ? "bg-gray-600 text-white" : "text-gray-500 hover:text-gray-300"}`}
        >
          Edit
        </button>
        <button
          onClick={() => setPreview(true)}
          className={`text-xs px-2 py-0.5 rounded ${preview ? "bg-gray-600 text-white" : "text-gray-500 hover:text-gray-300"}`}
        >
          Preview
        </button>
      </div>
      {preview ? (
        <div
          className="bg-gray-900 rounded-lg px-3 py-2 text-sm text-gray-200 min-h-32 prose prose-invert max-w-none"
          dangerouslySetInnerHTML={{ __html: renderPreview(value) }}
        />
      ) : (
        <textarea
          value={value || ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={rows}
          className="w-full bg-gray-900 text-white placeholder-gray-500
                     rounded-lg px-3 py-2 text-sm font-mono outline-none resize-y
                     focus:ring-1 focus:ring-amber-500 leading-relaxed"
        />
      )}
    </div>
  );
}

/** Queue item in the left sidebar */
function QueueItem({ run, isSelected, onClick }) {
  const reviewStatus = run.review_status || "pending";
  const statusColor =
    {
      pending: "bg-yellow-500",
      in_progress: "bg-blue-500",
      complete: "bg-green-500",
      skipped: "bg-gray-500",
    }[reviewStatus] || "bg-gray-500";

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-3 border-b border-gray-700
                  hover:bg-gray-700 transition-colors
                  ${isSelected ? "bg-gray-700 border-l-2 border-l-amber-400" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-medium truncate">
            {run.attendee_name || "Unknown"}
            {run.has_email && (
              <span
                className="ml-1 text-cyan-500"
                title="Has email for delivery"
              >
                ✉
              </span>
            )}
          </p>
          <p className="text-gray-400 text-xs mt-0.5 line-clamp-2 leading-tight">
            {run.question_preview}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span
            className={`${statusColor} text-white text-xs px-1.5 py-0.5 rounded-full`}
          >
            {reviewStatus}
          </span>
          <span className="text-gray-600 text-xs">{run.pipeline_version}</span>
        </div>
      </div>
      {run.publishable && (
        <p className="text-green-400 text-xs mt-1">✓ Approved</p>
      )}
    </button>
  );
}

// =============================================================
// Main view
// =============================================================

export default function BatchReview() {
  // Queue state
  const [runs, setRuns] = useState([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("pending");

  // Selected run state
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Edit state — populated when a run is loaded
  const [scores, setScores] = useState({});
  const [flags, setFlags] = useState({});
  const [meta, setMeta] = useState({
    edit_effort: null,
    moderation_correct: null,
    final_answer: "",
    review_notes: "",
  });

  // Action state
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);
  // null | 'saved' | 'approved' | 'error'

  // Which artifact panel is shown in the diff view
  const [diffPanel, setDiffPanel] = useState("voice_draft");
  // 'factual_draft' | 'educational_draft' | 'voice_draft'

  // =============================================================
  // Data fetching
  // =============================================================

  const fetchQueue = useCallback(async () => {
    try {
      const params = statusFilter !== "all" ? { status: statusFilter } : {};
      const res = await axios.get("/api/review/runs", { params });
      setRuns(res.data.runs || []);
    } catch (err) {
      console.error("Failed to fetch review queue", err);
    } finally {
      setQueueLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchQueue();
  }, [fetchQueue]);

  const loadRunDetail = useCallback(async (runId) => {
    setDetailLoading(true);
    setRunDetail(null);
    setSaveStatus(null);
    try {
      const res = await axios.get(`/api/review/runs/${runId}`);
      const data = res.data;
      setRunDetail(data);

      // Populate edit state from loaded review
      const review = data.human_review || {};
      const humanScores = review.human_scores || {};
      const activeFlags = review.active_flags || [];

      setScores(
        Object.fromEntries(
          SCORE_DIMENSIONS.map(({ key }) => [key, humanScores[key] ?? null]),
        ),
      );
      setFlags(flagsFromActiveList(activeFlags));
      setMeta({
        edit_effort: review.edit_effort ?? null,
        moderation_correct: review.moderation_correct ?? null,
        final_answer:
          review.final_answer || data.artifacts?.voice_draft?.content || "",
        review_notes: review.review_notes || "",
      });
    } catch (err) {
      console.error("Failed to load run detail", err);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedRunId) loadRunDetail(selectedRunId);
  }, [selectedRunId, loadRunDetail]);

  // =============================================================
  // Actions
  // =============================================================

  const handleSave = async () => {
    if (!selectedRunId) return;
    setSaving(true);
    setSaveStatus(null);
    try {
      const body = buildPatchBody(scores, flags, meta);
      await axios.patch(`/api/review/runs/${selectedRunId}`, body);
      setSaveStatus("saved");
      await fetchQueue();
    } catch (err) {
      console.error("Save failed", err);
      setSaveStatus("error");
    } finally {
      setSaving(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedRunId || !meta.final_answer?.trim()) return;
    setApproving(true);
    setSaveStatus(null);
    try {
      const res = await axios.post(
        `/api/review/runs/${selectedRunId}/approve`,
        {
          final_answer: meta.final_answer.trim(),
          review_notes: meta.review_notes || null,
        },
      );
      setSaveStatus("approved");
      // Reload detail to reflect complete status
      await loadRunDetail(selectedRunId);
      await fetchQueue();
      return res.data;
    } catch (err) {
      console.error("Approve failed", err);
      setSaveStatus("error");
    } finally {
      setApproving(false);
    }
  };

  const handleExportSingle = () => {
    if (!selectedRunId) return;
    window.open(`/api/review/runs/${selectedRunId}/export?fmt=csv`, "_blank");
  };

  const handleExportBulk = () => {
    const params = statusFilter !== "all" ? `?status=${statusFilter}` : "";
    window.open(`/api/review/export${params}&fmt=csv`, "_blank");
  };

  const handleFlagChange = (key, value) => {
    setFlags((prev) => ({ ...prev, [key]: value }));
  };

  const handleScoreChange = (key, value) => {
    setScores((prev) => ({ ...prev, [key]: value }));
  };

  // =============================================================
  // Derived state
  // =============================================================

  const isApproved = runDetail?.human_review?.review_status === "complete";
  const hasEmail = runDetail?.question?.has_email;
  const canApprove = meta.final_answer?.trim().length > 0 && !isApproved;

  const llmScores = runDetail?.llm_scores || {};
  const getLLMScore = (phase, name) => llmScores[phase]?.[name]?.value ?? null;

  const artifacts = runDetail?.artifacts || {};
  const availablePanels = [
    { key: "factual_draft", label: "Factual draft" },
    { key: "educational_draft", label: "Educational draft" },
    { key: "voice_draft", label: "Voice draft" },
  ].filter((p) => artifacts[p.key]?.content);

  // =============================================================
  // Render
  // =============================================================

  return (
    <div className="min-h-screen bg-gray-900 text-white flex flex-col">
      {/* ── Header ── */}
      <div className="bg-gray-800 border-b border-gray-700 px-4 py-3 flex items-center gap-3">
        <TechbearAvatar size="sm" border={false} />
        <div>
          <p className="text-white font-semibold text-sm">Batch Review</p>
          <p className="text-gray-400 text-xs">Async pipeline output</p>
        </div>
        <div className="ml-auto flex gap-2">
          <button
            onClick={handleExportBulk}
            className="text-xs text-gray-400 hover:text-white border border-gray-600
                       hover:border-gray-400 px-3 py-1 rounded transition-colors"
          >
            Export CSV
          </button>
          <a
            href="/dashboard"
            className="text-xs text-gray-400 hover:text-white border border-gray-600
                       hover:border-gray-400 px-3 py-1 rounded transition-colors"
          >
            Live dashboard
          </a>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* ── Left: Queue ── */}
        <div className="w-72 border-r border-gray-700 flex flex-col shrink-0">
          {/* Status filter tabs */}
          <div className="px-3 py-2 border-b border-gray-700 flex flex-wrap gap-1">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setStatusFilter(f)}
                className={`text-xs px-2 py-0.5 rounded-full transition-colors ${
                  statusFilter === f
                    ? "bg-amber-500 text-gray-900 font-medium"
                    : "text-gray-400 hover:text-white"
                }`}
              >
                {f}
              </button>
            ))}
          </div>

          {/* Queue list */}
          <div className="flex-1 overflow-y-auto">
            {queueLoading ? (
              <p className="text-gray-500 text-xs text-center p-6">
                Loading queue...
              </p>
            ) : runs.length === 0 ? (
              <p className="text-gray-500 text-xs text-center p-6">
                No runs match this filter.
              </p>
            ) : (
              runs.map((run) => (
                <QueueItem
                  key={run.run_id}
                  run={run}
                  isSelected={run.run_id === selectedRunId}
                  onClick={() => setSelectedRunId(run.run_id)}
                />
              ))
            )}
          </div>

          <div className="border-t border-gray-700 px-3 py-2">
            <p className="text-gray-600 text-xs text-center">
              {runs.length} run{runs.length !== 1 ? "s" : ""}
            </p>
          </div>
        </div>

        {/* ── Right: Detail panel ── */}
        <div className="flex-1 overflow-y-auto">
          {!selectedRunId ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-600 gap-3">
              <span className="text-4xl">🐾</span>
              <p className="text-sm">
                Select a run from the queue to review it.
              </p>
            </div>
          ) : detailLoading ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-gray-500 text-sm">Loading run...</p>
            </div>
          ) : runDetail ? (
            <div className="p-5 flex flex-col gap-6 max-w-5xl">
              {/* ── Question header ── */}
              <div className="bg-gray-800 rounded-xl p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <p className="text-xs text-gray-500 mb-1">
                      {runDetail.pipeline_version}
                      {runDetail.run_label && ` · ${runDetail.run_label}`}
                      {" · "}
                      {runDetail.status}
                    </p>
                    <p className="text-sm font-medium text-gray-300 mb-1">
                      {runDetail.question?.attendee_name}
                      {hasEmail && (
                        <span
                          className="ml-2 text-cyan-500 text-xs"
                          title="Answer will be emailed on approval"
                        >
                          ✉ email on approve
                        </span>
                      )}
                    </p>
                    <p className="text-white text-base leading-relaxed">
                      {runDetail.question?.question_text}
                    </p>
                  </div>
                  {isApproved && (
                    <span className="bg-green-600 text-white text-xs px-2 py-1 rounded-full shrink-0">
                      ✓ Approved
                    </span>
                  )}
                </div>
              </div>

              {/* ── Draft diff view ── */}
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-gray-300">
                    Pipeline output
                  </h2>
                  <div className="flex gap-1 ml-auto">
                    {availablePanels.map((p) => (
                      <button
                        key={p.key}
                        onClick={() => setDiffPanel(p.key)}
                        className={`text-xs px-2 py-0.5 rounded transition-colors ${
                          diffPanel === p.key
                            ? "bg-gray-600 text-white"
                            : "text-gray-500 hover:text-gray-300"
                        }`}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  {/* Left: selected pipeline artifact (read only) */}
                  <div className="bg-gray-800 rounded-xl p-4">
                    <p className="text-xs text-gray-500 mb-2 font-medium uppercase tracking-wide">
                      {availablePanels.find((p) => p.key === diffPanel)
                        ?.label || diffPanel}
                    </p>
                    <pre className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed font-sans">
                      {artifacts[diffPanel]?.content || (
                        <span className="text-gray-600 italic">
                          Not available
                        </span>
                      )}
                    </pre>
                  </div>

                  {/* Right: editable final answer */}
                  <div className="bg-gray-800 rounded-xl p-4">
                    <p className="text-xs text-gray-500 mb-2 font-medium uppercase tracking-wide">
                      Final answer (Markdown)
                    </p>
                    <MarkdownEditor
                      value={meta.final_answer}
                      onChange={(v) =>
                        setMeta((prev) => ({ ...prev, final_answer: v }))
                      }
                      placeholder="Edit the voice draft here. Markdown supported.&#10;This is what gets emailed and ingested into the corpus."
                      rows={12}
                    />
                  </div>
                </div>
              </div>

              {/* ── Scores ── */}
              <div className="bg-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-semibold text-gray-300 mb-3">
                  Scores
                  <span className="text-xs text-gray-500 font-normal ml-2">
                    cyan = LLM · amber = human
                  </span>
                </h2>
                <div className="flex flex-col gap-1">
                  {SCORE_DIMENSIONS.map((dim) => (
                    <ScoreRow
                      key={dim.key}
                      dimension={dim}
                      llmScore={getLLMScore(
                        dim.key
                          .replace("_score", "")
                          .replace("fact", "fact_critique")
                          .replace("character", "character_critique")
                          .replace("editorial", "editorial_critique")
                          .replace("semantic", "semantic_check")
                          .replace("educational", "educational_pass"),
                        dim.key,
                      )}
                      humanScore={scores[dim.key]}
                      onChange={(v) => handleScoreChange(dim.key, v)}
                    />
                  ))}
                </div>
              </div>

              {/* ── Flags ── */}
              <div className="bg-gray-800 rounded-xl p-4">
                <h2 className="text-sm font-semibold text-gray-300 mb-3">
                  Flags
                </h2>
                <div className="grid grid-cols-2 gap-x-6 gap-y-2">
                  {FLAG_DEFINITIONS.map((def) => (
                    <FlagCheckbox
                      key={def.key}
                      definition={def}
                      checked={flags[def.key]}
                      onChange={handleFlagChange}
                    />
                  ))}
                </div>
              </div>

              {/* ── Review metadata ── */}
              <div className="bg-gray-800 rounded-xl p-4 flex flex-col gap-4">
                <h2 className="text-sm font-semibold text-gray-300">
                  Review metadata
                </h2>

                {/* Edit effort */}
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-gray-400">Edit effort</label>
                  <div className="flex gap-2 flex-wrap">
                    {Object.entries(EDIT_EFFORT_LABELS).map(([val, label]) => (
                      <button
                        key={val}
                        onClick={() =>
                          setMeta((prev) => ({
                            ...prev,
                            edit_effort: parseInt(val),
                          }))
                        }
                        className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                          meta.edit_effort === parseInt(val)
                            ? parseInt(val) >= 3
                              ? "bg-red-700 border-red-500 text-white"
                              : parseInt(val) === 0
                                ? "bg-green-700 border-green-500 text-white"
                                : "bg-amber-700 border-amber-500 text-white"
                            : "border-gray-600 text-gray-400 hover:text-white hover:border-gray-400"
                        }`}
                      >
                        {val} — {label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Moderation correct */}
                <div className="flex items-center gap-4">
                  <span className="text-xs text-gray-400">
                    Moderation correct?
                  </span>
                  {[true, false].map((val) => (
                    <label
                      key={String(val)}
                      className="flex items-center gap-1.5 cursor-pointer"
                    >
                      <input
                        type="radio"
                        checked={meta.moderation_correct === val}
                        onChange={() =>
                          setMeta((prev) => ({
                            ...prev,
                            moderation_correct: val,
                          }))
                        }
                        className="accent-amber-400"
                      />
                      <span className="text-xs text-gray-300">
                        {val ? "Yes" : "No"}
                      </span>
                    </label>
                  ))}
                  {meta.moderation_correct === null && (
                    <span className="text-xs text-gray-600">not set</span>
                  )}
                </div>

                {/* Review notes */}
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-gray-400">
                    Reviewer notes
                    <span className="text-gray-600 ml-1">
                      (free text — not emailed)
                    </span>
                  </label>
                  <textarea
                    value={meta.review_notes}
                    onChange={(e) =>
                      setMeta((prev) => ({
                        ...prev,
                        review_notes: e.target.value,
                      }))
                    }
                    placeholder="Notes on this run — what went right, what went wrong, what to tune..."
                    rows={3}
                    className="w-full bg-gray-900 text-white placeholder-gray-600
                               rounded-lg px-3 py-2 text-sm outline-none resize-y
                               focus:ring-1 focus:ring-gray-500"
                  />
                </div>
              </div>

              {/* ── Action bar ── */}
              <div className="bg-gray-800 rounded-xl p-4 flex items-center gap-3 flex-wrap">
                {/* Save in progress */}
                <button
                  onClick={handleSave}
                  disabled={saving || isApproved}
                  className="bg-gray-600 hover:bg-gray-500 disabled:bg-gray-700
                             disabled:text-gray-500 disabled:cursor-not-allowed
                             text-white text-sm px-5 py-2 rounded-lg transition-colors"
                >
                  {saving ? "Saving..." : "Save draft"}
                </button>

                {/* Approve */}
                <button
                  onClick={handleApprove}
                  disabled={approving || !canApprove}
                  className="bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700
                             disabled:text-gray-500 disabled:cursor-not-allowed
                             text-white text-sm px-5 py-2 rounded-lg transition-colors font-medium"
                >
                  {approving
                    ? "Approving..."
                    : isApproved
                      ? "✓ Approved"
                      : hasEmail
                        ? "Approve + email attendee"
                        : "Approve"}
                </button>

                {/* Export single run */}
                <button
                  onClick={handleExportSingle}
                  className="text-xs text-gray-400 hover:text-white ml-auto transition-colors"
                >
                  Export this run ↓
                </button>

                {/* Status feedback */}
                {saveStatus && (
                  <span
                    className={`text-xs ml-2 ${
                      saveStatus === "saved"
                        ? "text-cyan-400"
                        : saveStatus === "approved"
                          ? "text-green-400"
                          : "text-red-400"
                    }`}
                  >
                    {saveStatus === "saved"
                      ? "✓ Saved"
                      : saveStatus === "approved"
                        ? "✓ Approved"
                        : "✗ Error — check console"}
                  </span>
                )}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
