import { useEffect, useRef, useState } from 'react'
import Sidebar from './components/Sidebar'
import { UserMessage, AssistantMessage, RunningMessage, ErrorMessage } from './components/ChatMessage'
import { SourcesDrawer, QueryDrawer } from './components/Drawer'
import { askQuestion, fetchQuestions, fetchSnapshot } from './api'
import './App.css'

let idCounter = 0
function uid() {
  idCounter += 1
  return idCounter
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [drawer, setDrawer] = useState(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [snapshot, setSnapshot] = useState(null)
  const [snapshotError, setSnapshotError] = useState(null)
  const [quickQuestions, setQuickQuestions] = useState([])
  const [knownQuestions, setKnownQuestions] = useState([])
  const scrollRef = useRef(null)
  const timerRef = useRef(null)

  useEffect(() => {
    fetchSnapshot()
      .then(setSnapshot)
      .catch((e) => setSnapshotError(e.message || 'Snapshot unavailable'))
    fetchQuestions()
      .then((list) => {
        setKnownQuestions(list)
        setQuickQuestions(list.slice(0, 4).map((q) => q.question))
      })
      .catch(() => setQuickQuestions([]))
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, running])

  useEffect(() => {
    if (running) {
      const start = Date.now()
      timerRef.current = window.setInterval(() => setElapsed(Date.now() - start), 200)
    } else {
      window.clearInterval(timerRef.current)
    }
    return () => window.clearInterval(timerRef.current)
  }, [running])

  async function ask(text) {
    const value = (text ?? input).trim()
    if (!value || running) return
    setInput('')
    const userId = uid()
    setMessages((m) => [...m, { id: userId, role: 'user', text: value }])
    setRunning(true)
    setElapsed(0)
    const matched = knownQuestions.find((q) => q.question === value)
    try {
      const result = await askQuestion(value, matched?.id)
      setMessages((m) => [...m, { id: uid(), role: 'assistant', result }])
    } catch (error) {
      setMessages((m) => [...m, { id: uid(), role: 'error', text: error?.message || 'Unable to process the request.' }])
    } finally {
      setRunning(false)
    }
  }

  function newChat() {
    setMessages([])
    setSidebarOpen(false)
  }

  const conversations = messages
    .filter((m) => m.role === 'user')
    .map((m) => ({ id: m.id, title: m.text, subtitle: 'This session' }))
    .reverse()

  const activeDrawerResult = messages.filter((m) => m.role === 'assistant').at(-1)?.result

  return (
    <div className="app">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNewChat={newChat}
        conversations={conversations}
        snapshot={snapshot}
        snapshotError={snapshotError}
      />

      <div className="main">
        <header className="topbar">
          <button className="menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open menu">
            <svg width="18" height="18" viewBox="0 0 18 18"><path d="M2 5h14M2 9h14M2 13h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
          </button>
          <span className="topbar-title">Complaint Intelligence</span>
          <span className="snapshot-pill">
            <span className={`snapshot-dot ${snapshotError ? 'error' : ''}`} />
            {snapshot ? `${snapshot.snapshot_start} → ${snapshot.snapshot_end}` : snapshotError ? 'Snapshot unavailable' : 'Loading snapshot…'}
          </span>
        </header>

        <div className="chat-scroll" ref={scrollRef}>
          <div className="chat-inner">
            {messages.length === 0 && (
              <div className="empty">
                <div className="empty-mark">
                  <svg width="26" height="26" viewBox="0 0 16 16" fill="none"><path d="M8 1 9.7 5.9 14.5 7.6 9.7 9.3 8 14.2 6.3 9.3 1.5 7.6 6.3 5.9 8 1Z" fill="currentColor" /></svg>
                </div>
                <h1>What would you like to investigate?</h1>
                <p>Ask in plain English. Every answer shows the exact steps, query, and cited complaints behind it.</p>
                <div className="quick-grid">
                  {quickQuestions.map((q) => (
                    <button key={q} className="quick-card" onClick={() => ask(q)}>
                      <span>{q}</span>
                      <svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 8h10m0 0L9 4m4 4-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg) => {
              if (msg.role === 'user') return <UserMessage key={msg.id} text={msg.text} />
              if (msg.role === 'error') return <ErrorMessage key={msg.id} text={msg.text} />
              return (
                <AssistantMessage
                  key={msg.id}
                  result={msg.result}
                  onOpenSources={() => setDrawer({ type: 'sources', result: msg.result })}
                  onOpenQuery={() => setDrawer({ type: 'query', result: msg.result })}
                />
              )
            })}

            {running && <RunningMessage elapsedLabel={`${(elapsed / 1000).toFixed(1)}s`} />}
          </div>
        </div>

        <div className="composer-area">
          <div className="composer-inner">
            <div className="composer">
              <textarea
                rows={1}
                value={input}
                placeholder="Ask a question about consumer complaints…"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    ask()
                  }
                }}
              />
              <button className="send-btn" disabled={!input.trim() || running} onClick={() => ask()} aria-label="Send">
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M8 13V3M8 3 3.5 7.5M8 3l4.5 4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
            </div>
            <p className="composer-hint">Read-only analysis, grounded in the CFPB snapshot · Meridian may abstain when evidence is insufficient</p>
          </div>
        </div>
      </div>

      {drawer?.type === 'sources' && <SourcesDrawer result={drawer.result || activeDrawerResult} onClose={() => setDrawer(null)} />}
      {drawer?.type === 'query' && <QueryDrawer result={drawer.result || activeDrawerResult} onClose={() => setDrawer(null)} />}
    </div>
  )
}
