import { parseAnswerText } from '../lib/parseAnswer'

export function SourcesDrawer({ result, onClose }) {
  const items = result?.evidence?.retrieval?.evidence || []
  const { quotes } = parseAnswerText(result?.answer?.abstained ? null : result?.answer?.answer)
  const quoteById = new Map(quotes.map((q) => [q.complaintId, q.quote]))
  return (
    <Shell onClose={onClose} eyebrow="Narrative evidence" title="Supporting complaints" subtitle={`${items.length} retrieved narrative${items.length === 1 ? '' : 's'} behind this answer`}>
      {items.length === 0 && <p className="drawer-empty">No retrieved evidence is attached to this answer.</p>}
      <div className="source-list">
        {items.map((item) => {
          const cited = quoteById.get(item.complaint_id)
          const text = cited || item.excerpt
          return (
            <article key={item.complaint_id} className="source-card">
              <div className="source-card-head">
                <span className="source-id">#{item.complaint_id}</span>
                <span className="source-meta">
                  {item.metadata?.company || item.metadata?.brand_name || 'Unknown company'}
                  {item.metadata?.date_received ? ` · ${item.metadata.date_received}` : ''}
                </span>
              </div>
              {text ? (
                <p>“{truncate(text, 320)}”{!cited && item.truncated ? '…' : ''}</p>
              ) : (
                <p className="source-no-quote">No narrative excerpt is available for this item.</p>
              )}
              <div className="source-tags">
                {cited && <span className="source-tag source-tag-cited">Cited in answer</span>}
                {item.metadata?.issue && <span className="source-tag">{item.metadata.issue}</span>}
              </div>
            </article>
          )
        })}
      </div>
    </Shell>
  )
}

export function QueryDrawer({ result, onClose }) {
  const sql = result?.answer?.query
  const structured = result?.evidence?.structured
  return (
    <Shell onClose={onClose} eyebrow="Structured analysis" title="Executed read-only query" subtitle="Compiled to parameterized SQL against gold tables">
      {sql ? (
        <>
          <pre className="sql-block"><code>{sql}</code></pre>
          <div className="query-meta-row">
            <div><span>Rows returned</span><strong>{structured?.rows?.length ?? '—'}</strong></div>
            <div><span>Source</span><strong>{structured?.source_database?.split('/').pop() || 'gold'}</strong></div>
          </div>
          <button className="copy-btn" onClick={() => navigator.clipboard?.writeText(sql)}>Copy SQL</button>
        </>
      ) : (
        <p className="drawer-empty">No structured query was executed for this answer.</p>
      )}
    </Shell>
  )
}

function truncate(text, max) {
  const normalized = text.split(/\s+/).join(' ')
  return normalized.length > max ? `${normalized.slice(0, max).trim()}…` : normalized
}

function Shell({ onClose, eyebrow, title, subtitle, children }) {
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="drawer-panel" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <span className="drawer-eyebrow">{eyebrow}</span>
            <h2>{title}</h2>
            <p className="drawer-subtitle">{subtitle}</p>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  )
}
