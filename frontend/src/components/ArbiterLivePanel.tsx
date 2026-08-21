import type { LiveResponseView } from '../types'
import { useStreamingScroll } from '../useStreamingScroll'

interface ArbiterLivePanelProps {
  live: LiveResponseView
}

export function ArbiterLivePanel({ live }: ArbiterLivePanelProps) {
  const streaming = live.status === 'streaming'
  const interrupted = live.status === 'failed' && live.text.length > 0
  const liveScroll = useStreamingScroll(live.text, streaming)

  return (
    <section className="verdict verdict--live" aria-label="Arbitrage en direct">
      <header className="expert-header">
        <h2>Verdict de l’Arbitre</h2>
        <span className="arbiter-live-badge" aria-live="polite">
          {interrupted
            ? 'Interrompu — non validé'
            : 'Génération MiniMax en direct — validation en attente'}
        </span>
      </header>

      {live.text.length === 0 && !interrupted && (
        <p className="expert-running">Préparation de l’arbitrage…</p>
      )}

      {live.text.length > 0 && (
        <div
          className={`live-draft${interrupted ? ' live-draft--failed' : ''}`}
          aria-label="Brouillon live du verdict"
          tabIndex={0}
          ref={liveScroll.containerRef}
          onScroll={liveScroll.onScroll}
        >
          <p className="live-draft-text">{live.text}</p>
          {streaming && (
            <span className="live-cursor" aria-hidden="true" />
          )}
        </div>
      )}

      {interrupted && (
        <p className="live-draft-failed">Brouillon conservé, interrompu — non validé.</p>
      )}
    </section>
  )
}
