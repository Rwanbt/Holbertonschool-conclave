import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ExpertColumn } from './components/ExpertColumn'
import type { ExpertRun, ExpertRole, LiveResponseView } from './types'

function outputRun(role: ExpertRole): ExpertRun {
  return {
    role,
    status: 'completed',
    output: {
      role,
      summary: 'Résumé validé.',
      findings: [
        { title: 'Constat', evidence: 'preuve', impact: 'impact', priority: 'medium' },
        { title: 'Constat 2', evidence: 'preuve 2', impact: 'impact 2', priority: 'high' },
      ],
      score_label: 'Solide',
      score: 80,
      recommendations: ['Conserver'],
      unavailable_tools: [],
    },
    error_code: null,
  }
}

function liveView(overrides: Partial<LiveResponseView>): LiveResponseView {
  return {
    role: 'avocat',
    status: 'idle',
    text: '',
    lastSequence: 0,
    errorCode: null,
    ...overrides,
  }
}

describe('ExpertColumn — rendu live', () => {
  it('affiche « Préparation de l’analyse… » avant le premier delta', () => {
    const run: ExpertRun = {
      role: 'avocat',
      status: 'running',
      output: null,
      error_code: null,
    }
    const html = renderToStaticMarkup(
      <ExpertColumn role="avocat" run={run} live={liveView({ status: 'streaming' })} />,
    )
    expect(html).toContain('Préparation de l’analyse…')
  })

  it('affiche immédiatement le texte cumulé pendant le streaming', () => {
    const run: ExpertRun = {
      role: 'avocat',
      status: 'running',
      output: null,
      error_code: null,
    }
    const html = renderToStaticMarkup(
      <ExpertColumn
        role="avocat"
        run={run}
        live={liveView({ status: 'streaming', text: 'Le texte arrive au fil de l’eau', lastSequence: 4 })}
      />,
    )
    expect(html).toContain('Le texte arrive au fil de l’eau')
    expect(html).toContain('Génération MiniMax en direct — validation en attente')
    expect(html).toContain('live-cursor')
  })

  it('rend le texte live comme texte, jamais comme HTML', () => {
    const run: ExpertRun = {
      role: 'avocat',
      status: 'running',
      output: null,
      error_code: null,
    }
    const html = renderToStaticMarkup(
      <ExpertColumn
        role="avocat"
        run={run}
        live={liveView({ status: 'streaming', text: '<b>XSS</b>', lastSequence: 1 })}
      />,
    )
    expect(html).toContain('&lt;b&gt;XSS&lt;/b&gt;')
    expect(html).not.toContain('<b>XSS</b>')
  })

  it('la sortie validée prioritaire remplace le brouillon live', () => {
    const html = renderToStaticMarkup(
      <ExpertColumn
        role="avocat"
        run={outputRun('avocat')}
        live={liveView({ status: 'completed', text: 'brouillon jamais affiché', lastSequence: 9 })}
      />,
    )
    expect(html).toContain('Résumé validé.')
    expect(html).toContain('80/100')
    expect(html).not.toContain('brouillon jamais affiché')
    expect(html).not.toContain('live-cursor')
  })

  it('conserve le brouillon et le marque interrompu en cas d’échec du flux', () => {
    const run: ExpertRun = {
      role: 'procureur',
      status: 'running',
      output: null,
      error_code: null,
    }
    const html = renderToStaticMarkup(
      <ExpertColumn
        role="procureur"
        run={run}
        live={liveView({ role: 'procureur', status: 'failed', text: 'moitié du texte', lastSequence: 2, errorCode: 'protocol_error' })}
      />,
    )
    expect(html).toContain('moitié du texte')
    expect(html).toContain('interrompu — non validé')
  })
})