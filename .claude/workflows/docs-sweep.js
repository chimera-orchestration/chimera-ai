export const meta = {
  name: 'docs-sweep',
  description:
    'Audit AGENTS.md + agent-docs against the code: parallel claim auditors, adversarial verify, drift report',
  whenToUse:
    'The executable phase 1 of processes/docs-sweep.md — run when code and the agent docs may have drifted. Optional args: {docs: ["agent-docs/logging.md", ...]} to limit scope (default: AGENTS.md + all of agent-docs/). Returns verified findings; fixing them, the human-docs writer and Sybil pinning (phases 2-4) stay with the orchestrating session per the process file.',
  phases: [
    { title: 'Scout', detail: 'measure the docs; slice big ones at section boundaries' },
    { title: 'Audit', detail: 'one read-only auditor per slice, claim by claim' },
    { title: 'Verify', detail: 'adversarially re-verify every drift finding against the code' },
  ],
}

// ---------------------------------------------------------------- args
let a = args
if (typeof a === 'string') {
  try {
    a = JSON.parse(a)
  } catch (e) {
    a = null
  }
}
const requested = (a && a.docs) || null

// ---------------------------------------------------------------- scout
// Workflow scripts have no filesystem access, so a tiny agent measures the docs;
// slicing is then plain JS: whole doc when small, contiguous section runs of
// <=220 lines when not.
phase('Scout')

const SCOUT_SCHEMA = {
  type: 'object',
  properties: {
    docs: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string' },
          lines: { type: 'integer' },
          sections: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                title: { type: 'string' },
                start: { type: 'integer' },
                end: { type: 'integer' },
              },
              required: ['title', 'start', 'end'],
            },
          },
        },
        required: ['path', 'lines', 'sections'],
      },
    },
  },
  required: ['docs'],
}

const scope = requested
  ? `exactly these files: ${requested.join(', ')}`
  : 'AGENTS.md plus every agent-docs/*.md'
const scout = await agent(
  `In this repo, measure ${scope}. For each file report its path, total line count, and its ` +
    "'## ' section headings with each section's start line and end line (the line before the " +
    'next heading, or the last line). A file with no sections reports one section spanning the ' +
    "whole file. Use `git grep -n '^## '` and `wc -l` (never bare grep). Report only files that exist.",
  { label: 'scout:measure', schema: SCOUT_SCHEMA },
)

const MAX_SLICE = 220
const slices = []
for (const doc of scout.docs) {
  if (doc.lines <= MAX_SLICE) {
    slices.push({ path: doc.path, start: 1, end: doc.lines, titles: null })
    continue
  }
  let run = []
  let size = 0
  for (const section of doc.sections) {
    const sectionSize = section.end - section.start + 1
    if (run.length && size + sectionSize > MAX_SLICE) {
      slices.push({ path: doc.path, start: run[0].start, end: run[run.length - 1].end, titles: run.map((s) => s.title) })
      run = []
      size = 0
    }
    run.push(section)
    size += sectionSize
  }
  if (run.length) {
    slices.push({ path: doc.path, start: run[0].start, end: run[run.length - 1].end, titles: run.map((s) => s.title) })
  }
}
log(`${scout.docs.length} docs -> ${slices.length} audit slices`)

// ---------------------------------------------------------------- audit + verify
const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          verdict: { type: 'string', enum: ['DRIFTED', 'MISSING', 'UNVERIFIABLE'] },
          doc: { type: 'string' },
          line: { type: 'integer' },
          claim: { type: 'string' },
          reality: { type: 'string' },
        },
        required: ['verdict', 'doc', 'line', 'claim', 'reality'],
      },
    },
    confirmed: { type: 'integer' },
    absentFromVocabulary: {
      type: 'array',
      items: {
        type: 'object',
        properties: { concept: { type: 'string' }, evidence: { type: 'string' } },
        required: ['concept', 'evidence'],
      },
    },
  },
  required: ['findings', 'confirmed'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: { stands: { type: 'boolean' }, note: { type: 'string' } },
  required: ['stands', 'note'],
}

const auditPrompt = (slice) =>
  'You are a documentation-reality auditor for the chimera codebase (a Python CLI, package ' +
  '`chimera` under src/, tests under tests/). The worry is drift: doc claims that no longer ' +
  `match the code.\n\nASSIGNMENT: audit ${slice.path} lines ${slice.start}-${slice.end} ONLY` +
  (slice.titles ? ` (sections: ${slice.titles.join(', ')})` : '') +
  '. Other auditors cover the rest — do not stray.\n\n' +
  'METHOD:\n' +
  '1. Read your slice closely. Extract every independently checkable claim, by category: ' +
  'NAMES (commands, flags, module/attr paths, file paths, env vars, log-line messages, ref ' +
  'shapes, marker paths), BEHAVIOUR ("X refuses when Y", defaults, idempotence), TESTS ' +
  '("a test pins X" — find that exact test), CROSS-REFS to other docs/files.\n' +
  '2. Verify each against the actual code — never plausibility or memory. Use `git grep -n` ' +
  '(NEVER bare grep) and read src/chimera/** and tests/**. If you must run the CLI, ALWAYS pin ' +
  'a scratch workspace on the command itself: `CHIMERA_WORKSPACE=$(mktemp -d) uv run python -m ' +
  'chimera help -v` — a bare run can mutate the live workspace. Prefer reading code.\n' +
  '3. Verdicts: CONFIRMED (count only, do not itemise), DRIFTED (quote the doc, cite code ' +
  'file:line, state the actual behaviour), MISSING (the named thing exists nowhere), ' +
  'UNVERIFIABLE (needs external state — say why).\n' +
  '4. Calibration: paraphrase and undocumented extras are NOT drift; a named artifact absent ' +
  'under its exact name IS a finding even when something similar exists — names are ' +
  'load-bearing for agents.\n' +
  (slice.path === 'AGENTS.md'
    ? '5. REVERSE SWEEP (AGENTS.md only): report first-class concepts alive in the code but ' +
      'absent from the core-concepts vocabulary list, with evidence — nouns the CLI cannot ' +
      'self-describe (roles, artifacts), never mere commands (those stay discoverable via ch help).\n'
    : '') +
  '\nReport findings with doc/line/claim/reality filled in; put your CONFIRMED total in confirmed.'

const verifyPrompt = (finding) =>
  'Adversarially verify this documentation-drift finding against the chimera codebase ' +
  '(src/chimera/**, tests/**) — try to REFUTE it. Read the cited doc line and the actual code; ' +
  'use `git grep -n`, never bare grep; any CLI run must pin `CHIMERA_WORKSPACE=$(mktemp -d)`. ' +
  'If the doc claim is actually true as written, the finding falls (stands=false). If the doc ' +
  `really misleads, it stands.\n\nFinding: ${finding.doc}:${finding.line} [${finding.verdict}] ` +
  `claim: ${JSON.stringify(finding.claim)} — reality per auditor: ${JSON.stringify(finding.reality)}`

const audited = await pipeline(
  slices,
  (slice) =>
    agent(auditPrompt(slice), {
      label: `audit:${slice.path}:${slice.start}`,
      phase: 'Audit',
      schema: FINDINGS_SCHEMA,
    }),
  (report, slice) => {
    if (!report) return null
    const checkable = report.findings.filter((f) => f.verdict !== 'UNVERIFIABLE')
    return parallel(
      checkable.map((finding) => () =>
        agent(verifyPrompt(finding), {
          label: `verify:${finding.doc}:${finding.line}`,
          phase: 'Verify',
          schema: VERDICT_SCHEMA,
        }).then((v) => ({ ...finding, stands: v ? v.stands : true, note: v ? v.note : 'verifier died; treat as standing' })),
      ),
    ).then((verified) => ({
      slice: `${slice.path}:${slice.start}-${slice.end}`,
      confirmed: report.confirmed,
      unverifiable: report.findings.filter((f) => f.verdict === 'UNVERIFIABLE'),
      findings: verified.filter(Boolean),
      absentFromVocabulary: report.absentFromVocabulary || [],
    }))
  },
)

// ---------------------------------------------------------------- report
const reports = audited.filter(Boolean)
const findings = reports.flatMap((r) => r.findings)
const result = {
  confirmed: reports.reduce((n, r) => n + r.confirmed, 0),
  drift: findings.filter((f) => f.stands),
  refuted: findings.filter((f) => !f.stands),
  unverifiable: reports.flatMap((r) => r.unverifiable),
  absentFromVocabulary: reports.flatMap((r) => r.absentFromVocabulary),
  slicesAudited: reports.map((r) => r.slice),
  slicesLost: slices.length - reports.length,
}
log(`${result.confirmed} claims confirmed; ${result.drift.length} drift findings stand (${result.refuted.length} refuted)`)
return result
