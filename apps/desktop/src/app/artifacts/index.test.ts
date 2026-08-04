import { afterEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'
import type { SessionInfo, SessionMessage } from '@/types/hermes'

import { artifactImageSrc, collectArtifactsForSession, isArtifactPreviewable } from './artifact-utils'

function makeSession(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    ended_at: null,
    id: 'session-1',
    input_tokens: 0,
    is_active: false,
    last_active: 1000,
    message_count: 1,
    model: null,
    output_tokens: 0,
    preview: null,
    source: null,
    started_at: 1000,
    title: 'Session',
    tool_call_count: 0,
    ...overrides
  }
}

describe('collectArtifactsForSession', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
    $connection.set(null)
  })

  it('indexes plain https links from assistant text', () => {
    const artifacts = collectArtifactsForSession(makeSession(), [
      {
        content: 'Reference: https://example.com/docs/getting-started',
        role: 'assistant',
        timestamp: 2000
      }
    ])

    expect(artifacts).toHaveLength(1)
    expect(artifacts[0]).toMatchObject({
      href: 'https://example.com/docs/getting-started',
      kind: 'link',
      value: 'https://example.com/docs/getting-started'
    })
  })

  it('indexes http links present in tool JSON payloads', () => {
    const messages: SessionMessage[] = [
      {
        content: JSON.stringify({ source_url: 'https://example.com/changelog/latest' }),
        role: 'tool',
        timestamp: 3000
      }
    ]

    const artifacts = collectArtifactsForSession(makeSession({ id: 'session-2' }), messages)

    expect(artifacts).toHaveLength(1)
    expect(artifacts[0]).toMatchObject({
      href: 'https://example.com/changelog/latest',
      kind: 'link',
      value: 'https://example.com/changelog/latest'
    })
  })

  it('indexes common backtest report and dataset formats', () => {
    const artifacts = collectArtifactsForSession(
      makeSession({ cwd: '/workspace/strategy', profile: 'quant' }),
      [
      {
        content:
          'Outputs: ./backtest/report.html ./backtest/trades.parquet ./backtest/equity.jsonl ./backtest/summary.xlsx',
        role: 'assistant',
        timestamp: 2500
      }
      ]
    )

    expect(artifacts.map(artifact => artifact.value)).toEqual([
      './backtest/report.html',
      './backtest/trades.parquet',
      './backtest/equity.jsonl',
      './backtest/summary.xlsx'
    ])
    expect(artifacts.every(artifact => artifact.kind === 'file')).toBe(true)
    expect(artifacts.every(artifact => artifact.cwd === '/workspace/strategy')).toBe(true)
    expect(artifacts.every(artifact => artifact.profile === 'quant')).toBe(true)
    expect(artifacts.map(isArtifactPreviewable)).toEqual([true, false, true, false])
  })

  it('previews generated image files but not web images already shown in place', () => {
    const artifacts = collectArtifactsForSession(makeSession(), [
      {
        content: 'Generated ./backtest/equity.png and referenced https://example.com/benchmark.png',
        role: 'assistant',
        timestamp: 2500
      }
    ])

    expect(Object.fromEntries(artifacts.map(artifact => [artifact.value, isArtifactPreviewable(artifact)]))).toEqual({
      './backtest/equity.png': true,
      'https://example.com/benchmark.png': false
    })
  })

  it('resolves remote image artifact thumbnails through the desktop fs bridge', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path.startsWith('/api/fs/read-data-url?')) {
        return { dataUrl: 'data:image/jpeg;base64,cmVtb3Rl' }
      }

      throw new Error(`unexpected path ${path}`)
    })

    vi.stubGlobal('window', { hermesDesktop: { api } })
    $connection.set({ baseUrl: 'https://gw', mode: 'remote', token: 'secret' } as never)

    const path = '/Users/me/.hermes/skills/work-esab/references/images/manual-step03.jpeg'
    const downloadHref = `https://gw/api/files/download?path=${encodeURIComponent(path)}&token=secret`

    await expect(artifactImageSrc(path, downloadHref, '/Users/me/project', 'quant')).resolves.toBe(
      'data:image/jpeg;base64,cmVtb3Rl'
    )

    expect(api).toHaveBeenCalledWith({
      path: '/api/fs/read-data-url?path=%2FUsers%2Fme%2F.hermes%2Fskills%2Fwork-esab%2Freferences%2Fimages%2Fmanual-step03.jpeg',
      profile: 'quant'
    })
  })

  it('resolves relative remote image paths from their producing session cwd', async () => {
    const api = vi.fn(async () => ({ dataUrl: 'data:image/png;base64,cG5n' }))

    vi.stubGlobal('window', { hermesDesktop: { api } })
    $connection.set({ mode: 'remote', profile: 'other' } as never)

    await artifactImageSrc('./backtest/equity.png', 'file://./backtest/equity.png', '/srv/strategy', 'quant')

    expect(api).toHaveBeenCalledWith({
      path: '/api/fs/read-data-url?path=%2Fsrv%2Fstrategy%2Fbacktest%2Fequity.png',
      profile: 'quant'
    })
  })
})
