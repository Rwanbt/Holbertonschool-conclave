import { useState } from 'react'
import type { ProviderConnection } from '../useProviderConnection'

interface ProviderPanelProps {
  connection: ProviderConnection
}

function statusLabel(connection: ProviderConnection): string {
  if (connection.connection === 'testing') {
    return 'Test en cours…'
  }
  if (connection.oauthConnected) {
    return `${connection.selectedProvider?.label ?? ''} · connecté (OAuth)`
  }
  if (connection.connection === 'connected') {
    return `${connection.selectedProvider?.label ?? ''} · connecté`
  }
  if (connection.connection === 'unverified') {
    return `${connection.selectedProvider?.label ?? ''} · clé non vérifiable sans inférence`
  }
  if (connection.connection === 'error') {
    return `${connection.selectedProvider?.label ?? ''} · échec de connexion`
  }
  if (connection.apiKey.trim().length > 0) {
    return `${connection.selectedProvider?.label ?? ''} · clé saisie (non testée)`
  }
  return 'Aucun fournisseur connecté'
}

/**
 * Panneau « Fournisseur IA » — BYOK.
 *
 * La clé API est de type password, vit uniquement en mémoire, et n'est jamais
 * écrite dans le stockage navigateur, l'URL, le bundle ou les logs.
 */
export function ProviderPanel({ connection }: ProviderPanelProps) {
  const [showKey, setShowKey] = useState(false)

  const provider = connection.selectedProvider
  const model = connection.selectedModel
  const incompatible =
    model !== null &&
    (!model.supports_tools ||
      !model.supports_streaming ||
      !model.supports_structured_output)

  return (
    <section className="provider-panel" aria-label="Fournisseur IA">
      <header className="provider-header">
        <h2>Fournisseur IA</h2>
        <span
          className={`provider-status provider-status--${connection.connection}`}
          aria-live="polite"
        >
          {statusLabel(connection)}
        </span>
      </header>

      <p className="provider-note">
        CONCLAVE n’embarque aucune clé. Votre clé API est utilisée uniquement
        pour cette analyse, transmise au backend en mémoire, et jamais
        persistée ni journalisée. Elle est oubliée au rechargement de la page.
      </p>

      {connection.catalogStatus === 'error' && connection.catalogError !== null && (
        <p className="status-error">{connection.catalogError}</p>
      )}

      <div className="provider-fields">
        <label className="provider-field">
          <span>Fournisseur</span>
          <select
            value={connection.selectedProviderId ?? ''}
            onChange={(event) => connection.selectProvider(event.target.value)}
            disabled={connection.catalogStatus !== 'ready'}
          >
            {connection.providers.map((item) => (
              <option key={item.provider_id} value={item.provider_id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        <label className="provider-field">
          <span>Modèle</span>
          <select
            value={connection.selectedModelId ?? ''}
            onChange={(event) => connection.selectModel(event.target.value)}
            disabled={provider === null}
          >
            {(provider?.models ?? []).map((item) => (
              <option key={item.model_id} value={item.model_id}>
                {item.model_id}
              </option>
            ))}
          </select>
        </label>

        <label className="provider-field provider-field--key">
          <span>Clé API</span>
          <div className="provider-key-row">
            <input
              type={showKey ? 'text' : 'password'}
              value={connection.apiKey}
              onChange={(event) => connection.setApiKey(event.target.value)}
              placeholder="Collez votre clé API personnelle"
              autoComplete="off"
              spellCheck={false}
              aria-label="Clé API personnelle"
            />
            <button
              type="button"
              className="provider-key-toggle"
              onClick={() => setShowKey((current) => !current)}
              aria-label={showKey ? 'Masquer la clé' : 'Afficher la clé'}
            >
              {showKey ? 'Masquer' : 'Afficher'}
            </button>
          </div>
        </label>
      </div>

      <div className="provider-actions">
        {connection.oauthSupported && connection.oauthConfigured && (
          connection.oauthConnected ? (
            <button
              type="button"
              className="provider-oauth-connected"
              onClick={() => void connection.disconnectOAuth()}
            >
              {`Déconnecter ${provider?.label ?? 'OAuth'}`}
            </button>
          ) : (
            <button
              type="button"
              className="provider-oauth"
              onClick={connection.connectOAuth}
            >
              {`Se connecter avec ${provider?.label ?? 'OAuth'}`}
            </button>
          )
        )}
        <button
          type="button"
          onClick={() => void connection.test()}
          disabled={
            connection.connection === 'testing' ||
            connection.selectedProviderId === null ||
            connection.apiKey.trim() === ''
          }
        >
          Tester la connexion
        </button>
        <button
          type="button"
          onClick={connection.disconnect}
          disabled={connection.apiKey === '' && connection.connection === 'disconnected'}
        >
          Déconnecter
        </button>
      </div>

      {connection.oauthSupported && connection.oauthConfigured && (
        <p className="provider-message">
          {connection.oauthConnected
            ? 'Connexion OAuth officielle active : aucune clé API n’est nécessaire pour ce fournisseur.'
            : 'Ce fournisseur propose une connexion OAuth officielle (recommandée). Sinon, utilisez une clé API.'}
        </p>
      )}

      {connection.connectionMessage !== null && (
        <p
          className={
            connection.connection === 'error' ? 'status-error' : 'provider-message'
          }
        >
          {connection.connectionMessage}
        </p>
      )}

      {incompatible && (
        <p className="status-error">
          Ce modèle ne prend pas en charge toutes les capacités requises
          (outils, streaming, sortie structurée). Choisissez un autre modèle.
        </p>
      )}

      {provider !== null && connection.isReady && (
        <p className="provider-disclosure">
          Votre document sera transmis à <strong>{provider.label}</strong> via
          votre propre compte API. CONCLAVE ne peut pas garantir les conditions
          de confidentialité du fournisseur.
        </p>
      )}
    </section>
  )
}