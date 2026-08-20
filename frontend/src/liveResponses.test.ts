import { describe, expect, it } from 'vitest'
import {
  collectLiveResponses,
  collectResponseFlows,
  CLIENT_MAX_LIVE_CHARS,
} from './liveResponses'
import type { AgentRole, AnalysisEvent } from './types'

function event(overrides: Partial<AnalysisEvent>): AnalysisEvent {
  return {
    id: 1,
    type: 'agent.response.started',
    payload: { analysis_id: 'abc', role: 'avocat' },
    ...overrides,
  }
}

function delta(
  id: number,
  role: AgentRole,
  sequence: number,
  text: string,
): AnalysisEvent {
  return event({
    id,
    type: 'agent.response.delta',
    payload: { analysis_id: 'abc', role, sequence, delta: text },
  })
}

function started(id: number, role: AgentRole): AnalysisEvent {
  return event({
    id,
    type: 'agent.response.started',
    payload: { analysis_id: 'abc', role },
  })
}

function completed(id: number, role: AgentRole): AnalysisEvent {
  return event({
    id,
    type: 'agent.response.completed',
    payload: { analysis_id: 'abc', role },
  })
}

function failed(id: number, role: AgentRole, error_code: string): AnalysisEvent {
  return event({
    id,
    type: 'agent.response.failed',
    payload: { analysis_id: 'abc', role, error_code },
  })
}

describe('collectLiveResponses', () => {
  it('démarre avec une vue idle par rôle', () => {
    const views = collectLiveResponses([])
    expect(views.avocat.status).toBe('idle')
    expect(views.arbitre.status).toBe('idle')
    expect(views.procureur.text).toBe('')
  })

  it('started remet le rôle à zéro puis concatène les deltas dans l’ordre', () => {
    const views = collectLiveResponses([
      started(1, 'avocat'),
      delta(2, 'avocat', 1, 'Bon'),
      delta(3, 'avocat', 2, 'jour'),
    ])
    expect(views.avocat.status).toBe('streaming')
    expect(views.avocat.text).toBe('Bonjour')
    expect(views.avocat.lastSequence).toBe(2)
  })

  it('une séquence dupliquée ou décroissante ne duplique jamais le texte', () => {
    const views = collectLiveResponses([
      started(1, 'avocat'),
      delta(2, 'avocat', 1, 'Bon'),
      delta(3, 'avocat', 1, 'Bon'),
      delta(4, 'avocat', 2, 'jour'),
      delta(5, 'avocat', 1, 'Bon'),
    ])
    expect(views.avocat.text).toBe('Bonjour')
  })

  it('les trois experts et l’arbitre restent séparés même intercalés', () => {
    const views = collectLiveResponses([
      started(1, 'avocat'),
      delta(2, 'avocat', 1, 'A-'),
      started(3, 'procureur'),
      delta(4, 'procureur', 1, 'P-'),
      delta(5, 'avocat', 2, 'un'),
      delta(6, 'procureur', 2, 'deux'),
      delta(7, 'comptable', 1, 'C-'),
    ])
    expect(views.avocat.text).toBe('A-un')
    expect(views.procureur.text).toBe('P-deux')
    expect(views.comptable.text).toBe('C-')
    expect(views.arbitre.status).toBe('idle')
  })

  it('l’arbitre démarre à arbiter.started ou à son premier started', () => {
    const views = collectLiveResponses([event({ type: 'arbiter.started', payload: {} })])
    expect(views.arbitre.status).toBe('streaming')
    expect(views.arbitre.text).toBe('')

    const withOwnStarted = collectLiveResponses([
      started(9, 'arbitre'),
      delta(10, 'arbitre', 1, 'Verdict'),
    ])
    expect(withOwnStarted.arbitre.text).toBe('Verdict')
  })

  it('started puis completed ferme le brouillon', () => {
    const views = collectLiveResponses([
      started(1, 'avocat'),
      delta(2, 'avocat', 1, 'texte'),
      completed(3, 'avocat'),
    ])
    expect(views.avocat.status).toBe('completed')
    expect(views.avocat.text).toBe('texte')
  })

  it('un delta après completed est ignoré', () => {
    const events = [
      started(1, 'avocat'),
      delta(2, 'avocat', 1, 'texte'),
      completed(3, 'avocat'),
      delta(4, 'avocat', 2, 'supplément'),
    ]
    const views = collectLiveResponses(events)
    expect(views.avocat.status).toBe('completed')
    expect(views.avocat.text).toBe('texte')
  })

  it('failed conserve le texte reçu et le marque non validé', () => {
    const views = collectLiveResponses([
      started(1, 'comptable'),
      delta(2, 'comptable', 1, 'coût'),
      delta(3, 'comptable', 2, ' évalué'),
      failed(4, 'comptable', 'protocol_error'),
    ])
    expect(views.comptable.status).toBe('failed')
    expect(views.comptable.text).toBe('coût évalué')
    expect(views.comptable.errorCode).toBe('protocol_error')
  })

  it('rejouer depuis zéro reconstruit exactement l’état final (F5)', () => {
    const fullFlow: readonly AnalysisEvent[] = [
      started(1, 'avocat'),
      delta(2, 'avocat', 1, 'La '),
      delta(3, 'avocat', 2, 'solution '),
      delta(4, 'avocat', 3, 'est défendable'),
      completed(5, 'avocat'),
    ]
    const replay = collectLiveResponses(fullFlow)
    expect(replay.avocat.text).toBe('La solution est défendable')
    expect(replay.avocat.lastSequence).toBe(3)
    expect(replay.avocat.status).toBe('completed')
  })

  it('borne défensivement le texte côté client', () => {
    const events: AnalysisEvent[] = [started(1, 'avocat')]
    let id = 2
    for (let sequence = 1; sequence <= 5; sequence += 1) {
      events.push(delta(id, 'avocat', sequence, 'x'.repeat(6000)))
      id += 1
    }
    const views = collectLiveResponses(events)
    expect(views.avocat.text.length).toBeLessThanOrEqual(CLIENT_MAX_LIVE_CHARS)
  })
})

describe('collectResponseFlows', () => {
  it('compte les deltas, caractères et premières/dernières séquences', () => {
    const flows = collectResponseFlows([
      started(1, 'avocat'),
      delta(2, 'avocat', 1, 'aa'),
      delta(3, 'avocat', 2, 'bb'),
      delta(4, 'avocat', 3, 'cc'),
      completed(5, 'avocat'),
      delta(6, 'arbitre', 1, 'v'),
    ])
    expect(flows.avocat.deltas).toBe(3)
    expect(flows.avocat.chars).toBe(6)
    expect(flows.avocat.firstSequence).toBe(1)
    expect(flows.avocat.lastSequence).toBe(3)
    expect(flows.avocat.status).toBe('completed')
    expect(flows.arbitre.deltas).toBe(1)
  })
})