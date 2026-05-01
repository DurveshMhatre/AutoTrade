import { useState, useEffect, useRef } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { CheckCircle, XCircle, Brain } from 'lucide-react'

/**
 * AgentDecisionsLog — Recent AI agent decisions with confidence bars,
 * signal badges, approved/rejected icons, and flash-in animation.
 *
 * Props:
 *   decisions — array of decision objects
 *   loading   — boolean
 */
export default function AgentDecisionsLog({ decisions = [], loading = false }) {
  const [flashId, setFlashId] = useState(null)
  const prevLenRef = useRef(decisions.length)

  // Flash new decisions as they arrive
  useEffect(() => {
    if (decisions.length > prevLenRef.current && decisions.length > 0) {
      setFlashId(decisions[0]?.id || 0)
      const timer = setTimeout(() => setFlashId(null), 1200)
      return () => clearTimeout(timer)
    }
    prevLenRef.current = decisions.length
  }, [decisions.length])

  // Format timestamp
  const formatTime = (ts) => {
    if (!ts) return '—'
    try {
      const date = typeof ts === 'number'
        ? new Date(ts > 1e12 ? ts : ts * 1000)
        : new Date(ts)
      return formatDistanceToNow(date, { addSuffix: true })
    } catch {
      return String(ts)
    }
  }

  // Last decision time
  const lastDecisionTime = decisions.length > 0
    ? formatTime(decisions[0]?.timestamp)
    : 'never'

  // Skeleton loading
  if (loading) {
    return (
      <div>
        <div className="card-title">Agent Decisions</div>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
            <div className="skeleton skeleton-text" style={{ width: '80%' }}></div>
            <div className="skeleton skeleton-text" style={{ width: '50%', marginTop: 4 }}></div>
          </div>
        ))}
      </div>
    )
  }

  // Empty state
  if (!decisions || decisions.length === 0) {
    return (
      <div>
        <div className="card-title">Agent Decisions</div>
        <div className="empty-state">
          <Brain size={32} strokeWidth={1.5} />
          <p>No agent decisions yet</p>
          <p style={{ fontSize: 11, marginTop: 4 }}>Waiting for orchestrator analysis...</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="decisions-header">
        <div className="card-title" style={{ marginBottom: 0 }}>Agent Decisions</div>
        <span className="decisions-subtitle">Last: {lastDecisionTime}</span>
      </div>

      <table className="data-table compact">
        <thead>
          <tr>
            <th>Time</th>
            <th>Signal</th>
            <th>Confidence</th>
            <th>Reason</th>
            <th style={{ textAlign: 'center' }}>OK</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((d, i) => {
            const signalLower = d.signal?.toLowerCase() || 'hold'
            const confPct = Math.round((d.confidence || 0) * 100)
            const isFlashing = (d.id || i) === flashId

            return (
              <tr
                key={d.id || i}
                className={`${!d.approved ? 'row-rejected' : ''} ${isFlashing ? 'flash-in' : ''}`}
              >
                <td className="time-ago">{formatTime(d.timestamp)}</td>
                <td>
                  <span className={`badge ${signalLower}`}>
                    {d.signal || 'HOLD'}
                  </span>
                </td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, minWidth: 30 }}>
                      {confPct}%
                    </span>
                    <div className="confidence-bar">
                      <div
                        className={`confidence-bar-fill ${signalLower}`}
                        style={{ width: `${confPct}%` }}
                      />
                    </div>
                  </div>
                </td>
                <td>
                  <span
                    className="tooltip-text"
                    data-tooltip={d.reason || 'No reason'}
                    style={{
                      maxWidth: 100,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      display: 'inline-block',
                      fontSize: 11,
                    }}
                  >
                    {d.reason
                      ? (d.reason.length > 20 ? d.reason.slice(0, 20) + '…' : d.reason)
                      : '—'
                    }
                  </span>
                </td>
                <td style={{ textAlign: 'center' }}>
                  {d.approved ? (
                    <CheckCircle size={14} className="approved-icon" />
                  ) : (
                    <XCircle size={14} className="rejected-icon" />
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
