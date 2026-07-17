import { describe, expect, it } from 'vitest'

import { isManagedTranscriptMessage } from '../domain/managedPresentation.js'
import type { Msg } from '../types.js'

const msg = (overrides: Partial<Msg>): Msg => ({ role: 'system', text: '', ...overrides })

describe('managed transcript presentation', () => {
  it('keeps customer messages and curated workspace panels', () => {
    expect(isManagedTranscriptMessage(msg({ role: 'user', text: 'Compare these companies' }))).toBe(true)
    expect(isManagedTranscriptMessage(msg({ role: 'assistant', text: 'Here is the comparison' }))).toBe(true)
    expect(isManagedTranscriptMessage(msg({ kind: 'intro' }))).toBe(true)
    expect(isManagedTranscriptMessage(msg({ kind: 'panel' }))).toBe(true)
    expect(isManagedTranscriptMessage(msg({ kind: 'slash' }))).toBe(true)
  })

  it('hides system, tool, reasoning, and todo trails', () => {
    expect(isManagedTranscriptMessage(msg({ role: 'system', text: 'system prompt' }))).toBe(false)
    expect(isManagedTranscriptMessage(msg({ role: 'tool', text: 'raw tool result' }))).toBe(false)
    expect(isManagedTranscriptMessage(msg({ kind: 'trail', thinking: 'private reasoning' }))).toBe(false)
    expect(isManagedTranscriptMessage(msg({ kind: 'trail', role: 'assistant', thinking: 'private reasoning' }))).toBe(false)
    expect(isManagedTranscriptMessage(msg({ kind: 'diff', role: 'assistant', text: 'raw file changes' }))).toBe(false)
    expect(isManagedTranscriptMessage(msg({ role: 'assistant', text: '[CONTEXT SUMMARY]: private handoff' }))).toBe(false)
    expect(isManagedTranscriptMessage(msg({ kind: 'trail', todos: [] }))).toBe(false)
  })
})