import { describe, expect, it } from 'vitest'

import { artWidth, parseRichMarkup } from '../banner.js'

describe('rich banner markup', () => {
  it('keeps multiple colored runs on one physical line', () => {
    const lines = parseRichMarkup(
      '[bold #2A8B78]│[/] [bold #E8F5F1]OXAIDE RESEARCH CENTER[/] [bold #2A8B78]│[/]'
    )

    expect(lines).toEqual([
      [
        ['#2A8B78', '│'],
        ['', ' '],
        ['#E8F5F1', 'OXAIDE RESEARCH CENTER'],
        ['', ' '],
        ['#2A8B78', '│']
      ]
    ])
    expect(artWidth(lines)).toBe(26)
  })

  it('preserves the physical row count of multi-color ASCII art', () => {
    const lines = parseRichMarkup(`[bold #5FD0B8]╭─◆──────────────────────╮[/]
[bold #2A8B78]│[/] [bold #E8F5F1]OXAIDE RESEARCH CENTER[/] [bold #2A8B78]│[/]
[bold #5FD0B8]╰────────────────────────╯[/]`)

    expect(lines).toHaveLength(3)
    expect(lines.map(runs => runs.map(([, text]) => text).join(''))).toEqual([
      '╭─◆──────────────────────╮',
      '│ OXAIDE RESEARCH CENTER │',
      '╰────────────────────────╯'
    ])
    expect(artWidth(lines)).toBe(26)
  })
})