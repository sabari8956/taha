const BASE = import.meta.env.VITE_API_BASE ?? ''

async function request(path, options) {
  const res = await fetch(`${BASE}${path}`, options)
  let body = null
  try {
    body = await res.json()
  } catch {
    // no JSON body
  }
  if (!res.ok) {
    const message = body?.error?.message || `Request failed (${res.status})`
    throw new Error(message)
  }
  return body
}

export function fetchSnapshot() {
  return request('/api/v1/snapshot')
}

export function fetchQuestions() {
  return request('/api/v1/questions')
}

let counter = 0
function nextId() {
  counter += 1
  return `ui-${Date.now().toString(36)}-${counter}`
}

export function askQuestion(question, questionId) {
  return request('/api/v1/answers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question_id: questionId || nextId(), question }),
  })
}
