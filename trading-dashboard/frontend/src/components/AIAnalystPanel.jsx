/**
 * AIAnalystPanel — Claude-powered trade analysis component.
 * ============================================================
 * Displays AI-generated trading performance reviews in a
 * premium research-report style. Supports 4 analysis types
 * and auto-loads cached daily summaries.
 */

import { useState, useEffect } from 'react'
import {
  Brain, Sparkles, AlertTriangle, CheckCircle2,
  Loader2, Shield, TrendingUp, Zap, Search
} from 'lucide-react'

const ANALYSIS_TYPES = [
  { key: 'daily_review', label: 'Daily Review', icon: TrendingUp },
  { key: 'losing_streak', label: 'Losing Streak', icon: AlertTriangle },
  { key: 'strategy_check', label: 'Strategy Check', icon: Search },
  { key: 'full_audit', label: 'Full Audit', icon: Shield },
]

/**
 * Very lightweight inline markdown renderer.
 * Converts basic markdown (headers, bold, italic, lists, code)
 * to HTML for displaying Claude's response.
 */
function renderMarkdown(text) {
  if (!text) return ''
  return text
    // Code blocks
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="analyst-code"><code>$2</code></pre>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code class="analyst-inline-code">$1</code>')
    // Headers
    .replace(/^### (.+)$/gm, '<h4 class="analyst-h4">$1</h4>')
    .replace(/^## (.+)$/gm, '<h3 class="analyst-h3">$1</h3>')
    .replace(/^# (.+)$/gm, '<h2 class="analyst-h2">$1</h2>')
    // Bold + italic
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Numbered lists
    .replace(/^\d+\.\s+(.+)$/gm, '<li class="analyst-li">$1</li>')
    // Bullet lists
    .replace(/^[-*]\s+(.+)$/gm, '<li class="analyst-li">$1</li>')
    // Paragraphs
    .replace(/\n\n/g, '</p><p class="analyst-p">')
    .replace(/\n/g, '<br/>')
}

function RiskBadge({ score }) {
  if (!score || score === 0) return null
  const color = score >= 7 ? 'emerald' : score >= 4 ? 'amber' : 'rose'
  return (
    <div className={`risk-badge risk-${color}`}>
      <Shield size={14} />
      <span className="risk-score">{score}</span>
      <span className="risk-label">/10 Risk</span>
    </div>
  )
}

export default function AIAnalystPanel() {
  const [analysisType, setAnalysisType] = useState('daily_review')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [autoLoaded, setAutoLoaded] = useState(false)

  // Auto-load cached daily summary on mount
  useEffect(() => {
    const loadCached = async () => {
      try {
        const resp = await fetch('/api/agent/daily-summary')
        if (resp.ok) {
          const data = await resp.json()
          if (data.analysis && !data.analysis.includes('not configured') && !data.analysis.includes('Not Configured')) {
            setResult(data)
            setAutoLoaded(true)
          }
        }
      } catch {
        // Silently fail — user can trigger manually
      }
    }
    loadCached()
  }, [])

  const runAnalysis = async () => {
    setLoading(true)
    setError(null)
    setAutoLoaded(false)

    try {
      const resp = await fetch('/api/agent/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ analysis_type: analysisType }),
      })

      if (!resp.ok) {
        throw new Error(`API returned ${resp.status}`)
      }

      const data = await resp.json()
      setResult(data)
    } catch (err) {
      setError(err.message || 'Analysis failed — check your Anthropic API key in .env')
    } finally {
      setLoading(false)
    }
  }

  // Extract headline (first ## heading content or first bold text)
  const headline = result?.analysis
    ? (result.analysis.match(/^##?\s*\d*\.?\s*HEADLINE[:\s]*(.+)$/im)?.[1]
      || result.analysis.match(/\*\*(.{10,80})\*\*/)?.[1]
      || '')
    : ''

  // Extract "Watch Out For" section
  const watchOutMatch = result?.analysis?.match(
    /##?\s*\d*\.?\s*WATCH\s*OUT\s*(?:FOR)?[:\s]*([\s\S]*?)(?=\n##|\n#|$)/i
  )
  const watchOutText = watchOutMatch ? watchOutMatch[1].trim() : ''

  return (
    <div className="analyst-panel">
      {/* ── Header ──────────────────────────── */}
      <div className="analyst-header">
        <div className="analyst-title-row">
          <Brain size={20} className="analyst-icon" />
          <h2 className="analyst-title">AI Trade Analyst</h2>
          <span className="analyst-powered">
            <Sparkles size={12} />
            Powered by Claude
          </span>
        </div>
        {result?.generated_at && (
          <div className="analyst-timestamp">
            {autoLoaded ? 'Auto-refreshed' : 'Generated'} at{' '}
            {new Date(result.generated_at).toLocaleTimeString()}
            {result.cached && ' (cached)'}
          </div>
        )}
      </div>

      {/* ── Analysis Type Selector ──────────── */}
      <div className="analyst-type-selector">
        {ANALYSIS_TYPES.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            className={`analyst-type-btn ${analysisType === key ? 'active' : ''}`}
            onClick={() => setAnalysisType(key)}
            disabled={loading}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      {/* ── Run Button ──────────────────────── */}
      <div className="analyst-actions">
        <button
          className="analyst-run-btn"
          onClick={runAnalysis}
          disabled={loading}
        >
          {loading ? (
            <>
              <Loader2 size={16} className="analyst-spinner" />
              Analyzing...
            </>
          ) : (
            <>
              <Zap size={16} />
              Run Analysis
            </>
          )}
        </button>
        <span className="analyst-cost-hint">~$0.01 per analysis</span>
      </div>

      {/* ── Results ─────────────────────────── */}
      {error && (
        <div className="analyst-error">
          <AlertTriangle size={16} />
          {error}
        </div>
      )}

      {result && !error && (
        <div className="analyst-result">
          {/* Risk Score + Headline */}
          <div className="analyst-result-header">
            <RiskBadge score={result.risk_score} />
            {headline && (
              <div className="analyst-headline">{headline}</div>
            )}
          </div>

          {/* Full Analysis (rendered markdown) */}
          <div
            className="analyst-body"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(result.analysis) }}
          />

          {/* Recommendations Card */}
          {result.recommendations && result.recommendations.length > 0 && (
            <div className="analyst-recommendations">
              <div className="analyst-rec-title">
                <CheckCircle2 size={16} />
                Recommendations
              </div>
              <ul className="analyst-rec-list">
                {result.recommendations.map((rec, i) => (
                  <li key={i} className="analyst-rec-item">
                    <span className="analyst-rec-check">✓</span>
                    {rec}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Watch Out Card */}
          {watchOutText && (
            <div className="analyst-watchout">
              <div className="analyst-watchout-title">
                <AlertTriangle size={16} />
                Watch Out For
              </div>
              <p className="analyst-watchout-text">{watchOutText}</p>
            </div>
          )}
        </div>
      )}

      {/* ── Empty State ─────────────────────── */}
      {!result && !error && !loading && (
        <div className="analyst-empty">
          <Brain size={32} />
          <p>No analysis yet — run your first review</p>
          <p className="analyst-empty-sub">
            Select an analysis type above and click "Run Analysis"
          </p>
        </div>
      )}
    </div>
  )
}
