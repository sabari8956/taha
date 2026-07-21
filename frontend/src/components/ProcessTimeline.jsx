import { useState } from 'react'

const RUNNING_STEPS = [
  { id: 'coverage_check', label: 'Checking snapshot coverage' },
  { id: 'resolve_company', label: 'Resolving company entity' },
  { id: 'run_structured_analysis', label: 'Running structured analysis' },
  { id: 'retrieve_narratives', label: 'Retrieving narrative evidence' },
  { id: 'validate_draft', label: 'Validating citations & figures' },
]

export function RunningTimeline({ elapsedLabel }) {
  const [expanded, setExpanded] = useState(true)
  return (
    <div className="timeline is-running">
      <button className="timeline-toggle" onClick={() => setExpanded((v) => !v)}>
        <span className="timeline-toggle-left">
          <span className="pulse active" />
          Investigating your question
        </span>
        <span className="timeline-toggle-right">
          {elapsedLabel}
          <Chevron up={expanded} />
        </span>
      </button>
      {expanded && (
        <ol className="timeline-steps">
          {RUNNING_STEPS.map((step, i) => (
            <li key={step.id} className={`timeline-step ${i === 0 ? 'active' : 'pending'}`}>
              <span className="node">{i === 0 ? <span className="spinner" /> : <span className="dot" />}</span>
              <div className="step-text">
                <span className="step-label">{step.label}</span>
                {i === 0 && <span className="step-detail">Working…</span>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export default function ProcessTimeline({ steps, durationLabel }) {
  const [expanded, setExpanded] = useState(false)
  if (!steps || steps.length === 0) return null

  return (
    <div className="timeline is-done">
      <button className="timeline-toggle" onClick={() => setExpanded((v) => !v)}>
        <span className="timeline-toggle-left">
          <span className="pulse" />
          Investigation complete
        </span>
        <span className="timeline-toggle-right">
          {steps.length} steps{durationLabel ? ` · ${durationLabel}` : ''}
          <Chevron up={expanded} />
        </span>
      </button>
      {expanded && (
        <ol className="timeline-steps">
          {steps.map((step) => (
            <li key={step.id} className="timeline-step done">
              <span className="node">
                <svg width="11" height="11" viewBox="0 0 12 12"><path d="M2 6.2 4.8 9 10 2.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </span>
              <div className="step-text">
                <span className="step-label">{step.label}</span>
                {step.detail && <span className="step-detail">{step.detail}</span>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function Chevron({ up }) {
  return (
    <svg className={`chev ${up ? 'up' : ''}`} width="10" height="10" viewBox="0 0 10 10">
      <path d="M1 3.5 5 7l4-3.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
