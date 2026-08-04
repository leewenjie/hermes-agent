export const OXAIDE_RESEARCH_COMMANDS: [string, string][] = [
  ['/new', 'start new research'],
  ['/resume', 'open prior research'],
  ['/copy', 'copy the latest response'],
  ['/retry', 'run the last request again'],
  ['/title', 'name this research']
]

const OXAIDE_RESEARCH_COMMAND_NAMES = new Set([
  'help',
  // Used by the browser's trusted paste/drop bridge. Keep this functional but
  // out of customer command discovery.
  'image',
  ...OXAIDE_RESEARCH_COMMANDS.map(([command]) => command.slice(1))
])

export const isOxaideResearchCommand = (name: string): boolean =>
  OXAIDE_RESEARCH_COMMAND_NAMES.has(name.trim().replace(/^\//, '').toLowerCase())

export const OXAIDE_RESEARCH_SHORTCUTS: [string, string][] = [
  ['Shift+Enter', 'add a new line'],
  ['↑ / ↓', 'browse recent questions'],
  ['Ctrl+C', 'stop the current research response'],
  ['Ctrl+Shift+D', 'show or hide research details'],
  ['/details', 'expand or collapse reasoning steps']
]
