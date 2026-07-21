// Parses the deterministic template-rendered answer text produced by
// meridian_assistant.validation.render_validated to recover structured pieces
// for the UI, without re-implementing any backend logic.

const QUOTE_PATTERN = /Complaint (\d+) reported: [""]([^""]*)[""]/g
const INTERPRETATION_PREFIX = 'Interpretation: '
const LIMITATIONS_PREFIX = 'Limitations: '

export function parseAnswerText(text) {
  if (!text) return { sentences: [], quotes: [], interpretation: null, limitations: null }

  let body = text
  let interpretation = null
  let limitations = null

  const limIndex = body.indexOf(LIMITATIONS_PREFIX)
  if (limIndex !== -1) {
    limitations = body.slice(limIndex + LIMITATIONS_PREFIX.length).trim()
    body = body.slice(0, limIndex).trim()
  }
  const interpIndex = body.indexOf(INTERPRETATION_PREFIX)
  if (interpIndex !== -1) {
    interpretation = body.slice(interpIndex + INTERPRETATION_PREFIX.length).trim()
    body = body.slice(0, interpIndex).trim()
  }

  const quotes = []
  let match
  QUOTE_PATTERN.lastIndex = 0
  while ((match = QUOTE_PATTERN.exec(body)) !== null) {
    quotes.push({ complaintId: match[1], quote: match[2] })
  }

  const withoutQuotes = body.replace(QUOTE_PATTERN, '').trim()
  const sentences = withoutQuotes
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean)

  return { sentences, quotes, interpretation, limitations }
}

const LABEL_KEYS = ['issue', 'sub_issue', 'company_response_to_consumer', 'company']

export function buildFindings(structured) {
  if (!structured || !Array.isArray(structured.rows) || structured.rows.length === 0) return null
  const rows = structured.rows
  const first = rows[0]
  if (!('share' in first)) return null
  const labelKey = LABEL_KEYS.find((key) => key in first)
  if (!labelKey) return null
  return rows.slice(0, 5).map((row) => ({
    label: row[labelKey],
    value: Math.round((row.share || 0) * 1000) / 10,
  }))
}

export function metadataFor(evidence, complaintId) {
  const items = evidence?.retrieval?.evidence || []
  const match = items.find((item) => item.complaint_id === complaintId)
  return match?.metadata || null
}
