import ProcessTimeline, { RunningTimeline } from './ProcessTimeline'
import { buildFindings, parseAnswerText } from '../lib/parseAnswer'
import { describeStep } from '../data/steps'

export function UserMessage({ text }) {
  return (
    <div className="msg-row user">
      <div className="bubble user-bubble">{text}</div>
    </div>
  )
}

export function ErrorMessage({ text }) {
  return (
    <div className="msg-row assistant">
      <div className="avatar avatar-error">!</div>
      <div className="assistant-content">
        <div className="error-card">{text}</div>
      </div>
    </div>
  )
}

export function AssistantMessage({ result, onOpenSources, onOpenQuery }) {
  const { answer, tool_trace: trace, evidence } = result
  const steps = (trace?.steps || []).map((id) => describeStep(id, trace))
  const findings = evidence ? buildFindings(evidence.structured) : null
  const parsed = parseAnswerText(answer?.abstained ? null : answer?.answer)
  const hasCitations = answer?.citations && answer.citations.length > 0
  const hasQuery = Boolean(answer?.query)

  return (
    <div className="msg-row assistant">
      <div className={`avatar ${answer?.abstained ? 'avatar-abstain' : ''}`}>
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M8 1 9.7 5.9 14.5 7.6 9.7 9.3 8 14.2 6.3 9.3 1.5 7.6 6.3 5.9 8 1Z" fill="currentColor" /></svg>
      </div>
      <div className="assistant-content">
        <ProcessTimeline steps={steps} />

        {answer?.abstained ? (
          <div className="abstain-card">
            <span className="abstain-label">Abstained</span>
            <p>{answer.answer}</p>
          </div>
        ) : (
          <>
            {parsed.sentences.length > 0 && (
              <p className="answer-intro">{parsed.sentences[0]}</p>
            )}

            {findings && (
              <div className="findings-card">
                {findings.map((f, i) => (
                  <div className="finding-row" key={f.label}>
                    <span className="finding-index">{i + 1}</span>
                    <span className="finding-label">{f.label}</span>
                    <div className="finding-bar-track">
                      <div className="finding-bar" style={{ width: `${f.value}%` }} />
                    </div>
                    <span className="finding-value">{f.value}%</span>
                  </div>
                ))}
              </div>
            )}

            {parsed.sentences.slice(1).map((sentence, i) => (
              <p className="answer-detail" key={i}>{sentence}</p>
            ))}

            {parsed.interpretation && (
              <p className="answer-note"><b>Interpretation:</b> {parsed.interpretation}</p>
            )}
            {parsed.limitations && (
              <p className="answer-note"><b>Limitations:</b> {parsed.limitations}</p>
            )}

            {(hasCitations || hasQuery) && (
              <div className="answer-actions">
                {hasCitations && (
                  <div className="citation-pills">
                    {answer.citations.map((id) => (
                      <button key={id} className="citation-pill" onClick={onOpenSources}>#{id}</button>
                    ))}
                  </div>
                )}
                <div className="action-buttons">
                  {hasCitations && (
                    <button className="ghost-btn" onClick={onOpenSources}>
                      <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M2 8s2.5-4.5 6-4.5S14 8 14 8s-2.5 4.5-6 4.5S2 8 2 8Z" stroke="currentColor" strokeWidth="1.3" /><circle cx="8" cy="8" r="1.8" stroke="currentColor" strokeWidth="1.3" /></svg>
                      Sources
                    </button>
                  )}
                  {hasQuery && (
                    <button className="ghost-btn" onClick={onOpenQuery}>
                      <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M5 3 1.5 8 5 13M11 3l3.5 5-3.5 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
                      Query
                    </button>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export function RunningMessage({ elapsedLabel }) {
  return (
    <div className="msg-row assistant">
      <div className="avatar pulse-avatar">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M8 1 9.7 5.9 14.5 7.6 9.7 9.3 8 14.2 6.3 9.3 1.5 7.6 6.3 5.9 8 1Z" fill="currentColor" /></svg>
      </div>
      <div className="assistant-content">
        <RunningTimeline elapsedLabel={elapsedLabel} />
      </div>
    </div>
  )
}
