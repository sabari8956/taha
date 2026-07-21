// Railway injects VITE_API_BASE at build time. Keep the public backend as a
// showcase fallback so a missing build variable cannot silently call the
// frontend's Nginx server and parse index.html as API JSON.
const BASE = import.meta.env.VITE_API_BASE || 'https://backend-production-3c30.up.railway.app'

async function request(path, options) {
  const res = await fetch(`${BASE}${path}`, { cache: 'no-store', ...options })
  let body = null
  try {
    body = await res.json()
  } catch {
    // no JSON body
  }
  if (!body && !res.ok) throw new Error(`Request failed (${res.status})`)
  if (!body && res.ok) throw new Error('Backend returned an empty or invalid response')
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
  return request('/api/v1/questions').then((list) => {
    if (!Array.isArray(list)) throw new Error('Questions response was not a list')
    return list
  })
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
