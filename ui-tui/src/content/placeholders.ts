import { pick } from '../lib/text.js'

export const PLACEHOLDERS = [
  'Ask me anything…',
  'Try "explain this codebase"',
  'Try "write a test for…"',
  'Try "refactor the auth module"',
  'Try "/help" for commands',
  'Try "fix the lint errors"',
  'Try "how does the config loader work?"'
]

export const PLACEHOLDER = pick(PLACEHOLDERS)

export const OXAIDE_PLACEHOLDERS = [
  'Research a company, market, or investment theme…',
  'Compare two companies on returns, valuation, and risk…',
  'Review a thesis and identify missing evidence…',
  'Summarize the latest evidence on…',
  'Analyze the drawdown and recovery profile of…'
]

export const OXAIDE_PLACEHOLDER = pick(OXAIDE_PLACEHOLDERS)
