// Human-readable labels for the backend's real tool_trace.steps identifiers.
// See meridian_assistant.assistant.collect_evidence / execute_plan.
export const STEP_META = {
  coverage_check: {
    label: 'Checking snapshot coverage',
    detail: (t) =>
      t.coverage?.snapshot_start && t.coverage?.snapshot_end
        ? `Snapshot covers ${t.coverage.snapshot_start} through ${t.coverage.snapshot_end}`
        : t.coverage?.reason || 'Evaluated whether this question is answerable from the snapshot',
  },
  resolve_company: {
    label: 'Resolving company entity',
    detail: (t) =>
      t.entity_resolution
        ? `"${t.entity_resolution.input_value}" \u2192 ${t.entity_resolution.legal_company ?? 'unresolved'}`
        : 'Resolved brand name to legal entity',
  },
  run_structured_analysis: {
    label: 'Running structured analysis',
    detail: (t) =>
      t.structured_row_count
        ? `Query returned ${t.structured_row_count} row${t.structured_row_count === 1 ? '' : 's'} from gold aggregates`
        : 'Compiled and executed a read-only query against gold aggregates',
  },
  retrieve_narratives: {
    label: 'Retrieving narrative evidence',
    detail: (t) =>
      t.retrieved_evidence_count
        ? `Retrieved ${t.retrieved_evidence_count} filtered supporting complaint${t.retrieved_evidence_count === 1 ? '' : 's'}`
        : 'Applied metadata filters and retrieved supporting narratives',
  },
  synthesize: {
    label: 'Synthesizing answer',
    detail: () => 'Composed the answer strictly from collected tool evidence',
  },
  validate_draft: {
    label: 'Validating citations & figures',
    detail: (t) =>
      t.accepted_citation_count
        ? `Verified ${t.accepted_citation_count} citation${t.accepted_citation_count === 1 ? '' : 's'} against retrieved evidence`
        : 'Checked claims and citations against tool output',
  },
  planner_abstention: {
    label: 'Planner abstained',
    detail: () => 'The question could not be mapped to a supported evidence plan',
  },
  configuration_error: {
    label: 'Configuration check failed',
    detail: () => 'A required data or index dependency was unavailable',
  },
  synthesis_failure: {
    label: 'Synthesis failed',
    detail: () => 'The synthesis adapter could not produce a valid draft',
  },
}

export function describeStep(stepId, trace) {
  const meta = STEP_META[stepId] || { label: stepId, detail: () => '' }
  return { id: stepId, label: meta.label, detail: meta.detail(trace) }
}
