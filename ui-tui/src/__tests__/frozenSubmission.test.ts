import { describe, expect, it } from 'vitest'

import { frozenSubmissionAllowed } from '../app/useSubmission.js'

describe('frozenSubmissionAllowed', () => {
  it('keeps local copy available', () => {
    expect(frozenSubmissionAllowed(' /copy ')).toBe(true)
  })

  it('blocks prompts and work-producing commands', () => {
    expect(frozenSubmissionAllowed('research BTC liquidity')).toBe(false)
    expect(frozenSubmissionAllowed('/learn market making')).toBe(false)
    expect(frozenSubmissionAllowed('!pwd')).toBe(false)
  })
})