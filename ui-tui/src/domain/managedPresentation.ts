import type { Msg } from '../types.js'

const COMPACTION_PREFIXES = [
  '[CONTEXT COMPACTION — REFERENCE ONLY]',
  '[CONTEXT COMPACTION - REFERENCE ONLY]',
  '[CONTEXT SUMMARY]:'
]

export const isManagedTranscriptMessage = (msg: Msg): boolean =>
  !COMPACTION_PREFIXES.some(prefix => msg.text.trimStart().startsWith(prefix)) &&
  (msg.kind === 'intro' ||
    msg.kind === 'panel' ||
    msg.kind === 'slash' ||
    ((msg.role === 'user' || msg.role === 'assistant') && msg.kind !== 'diff' && msg.kind !== 'trail'))