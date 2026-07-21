export default function Sidebar({ open, onClose, onNewChat, conversations, activeId, snapshot, snapshotError }) {
  return (
    <>
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-top">
          <div className="logo">
            <span className="logo-mark">M</span>
            <div>
              <strong>Meridian</strong>
              <small>Complaint Intelligence</small>
            </div>
          </div>
          <button className="sidebar-close" onClick={onClose} aria-label="Close menu">✕</button>
        </div>

        <button className="new-chat-btn" onClick={onNewChat}>
          <span className="plus">+</span> New investigation
        </button>

        <div className="sidebar-section">
          <span className="sidebar-label">Conversations</span>
          <div className="history-list">
            {conversations.length === 0 && <p className="history-empty">No investigations yet</p>}
            {conversations.map((item) => (
              <button key={item.id} className={`history-item ${item.id === activeId ? 'active' : ''}`}>
                <span className="history-title">{item.title}</span>
                <span className="history-time">{item.subtitle}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="sidebar-footer">
          <span className="sidebar-label">Snapshot</span>
          {snapshotError ? (
            <p className="footer-error">{snapshotError}</p>
          ) : (
            <>
              <div className="footer-stat">
                <span>Date coverage</span>
                <strong>{snapshot ? `${snapshot.snapshot_start} → ${snapshot.snapshot_end}` : '—'}</strong>
              </div>
              <div className="footer-stat">
                <span>Narratives</span>
                <strong>{snapshot ? (snapshot.has_consumer_narratives ? 'Available' : 'Unavailable') : '—'}</strong>
              </div>
            </>
          )}
        </div>
      </aside>
      {open && <div className="sidebar-scrim" onClick={onClose} />}
    </>
  )
}
