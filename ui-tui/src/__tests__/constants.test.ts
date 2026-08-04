import { describe, expect, it } from 'vitest'

import { FACES } from '../content/faces.js'
import { HOTKEYS } from '../content/hotkeys.js'
import { OXAIDE_PLACEHOLDERS, PLACEHOLDERS } from '../content/placeholders.js'
import { OXAIDE_RESEARCH_COMMANDS, OXAIDE_RESEARCH_SHORTCUTS } from '../content/researchHelp.js'
import { TOOL_VERBS, VERBS } from '../content/verbs.js'
import { ROLE } from '../domain/roles.js'
import { ZERO } from '../domain/usage.js'
import { INTERPOLATION_RE } from '../protocol/interpolation.js'
import { DEFAULT_THEME } from '../theme.js'

describe('constants', () => {
  it('ZERO', () => expect(ZERO).toEqual({ calls: 0, input: 0, output: 0, total: 0 }))

  it('string arrays are populated', () => {
    for (const arr of [FACES, OXAIDE_PLACEHOLDERS, PLACEHOLDERS, VERBS]) {
      expect(arr.length).toBeGreaterThan(0)
      arr.forEach(s => expect(typeof s).toBe('string'))
    }
  })

  it('keeps Oxaide placeholders focused on research rather than coding', () => {
    expect(OXAIDE_PLACEHOLDERS.join(' ')).toMatch(/research|company|market|thesis/i)
    expect(OXAIDE_PLACEHOLDERS.join(' ')).not.toMatch(/codebase|lint|auth module|config loader/i)
  })

  it('keeps Oxaide help focused on customer research actions', () => {
    const help = [...OXAIDE_RESEARCH_COMMANDS, ...OXAIDE_RESEARCH_SHORTCUTS].flat().join(' ')
    expect(help).toMatch(/research|response|questions/i)
    // Raw reasoning/tool trails are deliberately filtered from the hosted
    // Oxaide transcript (see managedPresentation), so the hosted help does not
    // advertise a details/reasoning toggle. Stopping the run is a customer
    // research action; developer surfaces (shell/editor/gateway/model) stay out.
    expect(help).not.toMatch(/details|reasoning|quit|shell|editor|gateway|model|system prompt/i)
  })

  it('HOTKEYS are [key, desc] pairs', () => {
    HOTKEYS.forEach(([k, d]) => {
      expect(typeof k).toBe('string')
      expect(typeof d).toBe('string')
    })
  })

  it('documents Ctrl/Cmd+L as non-destructive redraw', () => {
    const hotkey = HOTKEYS.find(([k]) => k.endsWith('+L'))
    expect(hotkey).toBeDefined()
    expect(hotkey?.[1]).toBe('redraw / repaint')
  })

  it('TOOL_VERBS maps known tools (verb-only, no emoji)', () => {
    expect(TOOL_VERBS.terminal).toBe('terminal')
    expect(TOOL_VERBS.read_file).toBe('reading')
  })

  it('INTERPOLATION_RE matches {!cmd}', () => {
    INTERPOLATION_RE.lastIndex = 0
    expect(INTERPOLATION_RE.test('{!date}')).toBe(true)

    INTERPOLATION_RE.lastIndex = 0
    expect(INTERPOLATION_RE.test('plain')).toBe(false)
  })

  it('ROLE produces glyph/body/prefix per role', () => {
    for (const role of ['assistant', 'system', 'tool', 'user'] as const) {
      expect(ROLE[role](DEFAULT_THEME)).toHaveProperty('glyph')
    }
  })
})
