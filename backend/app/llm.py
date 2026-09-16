"""Passerelle de test du backend — le fournisseur passe par l'abstraction.

Ce module est le « tuyau » Palier 2 (jalon temporaire). Il ne connaît plus
aucun fournisseur concret : il utilise `providers.build_provider` comme
l'orchestrateur. `generate_answer` accepte une clé BYOK optionnelle (priorité)
et retombe sur la clé serveur uniquement si elle est autorisée
(`ALLOW_SERVER_PROVIDER_CREDENTIALS=true`).

Toute défaillance (réseau, timeout, statut HTTP d'erreur, réponse vide ou
illisible) est traduite en `ProviderError` normalisée : la route décide
ensuite du code HTTP à renvoyer. Rien de la réponse brute ni des en-têtes
fournisseur n'est exposé ici.
"""

from typing import Final

from .config import Settings, get_settings
from .providers import ProviderError, build_provider

SYSTEM_PROMPT: Final[str] = (
    "Tu es une passerelle de test du backend CONCLAVE. "
    "Réponds en français, en une ou deux phrases courtes, "
    "sans titre, sans liste ni Markdown complexe."
)


async def generate_answer(
    message: str,
    settings: Settings | None = None,
    *,
    provider_id: str = "minimax",
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    current = settings if settings is not None else get_settings()
    provider = build_provider(
        provider_id=provider_id,
        model_id=model or current.minimax_model,
        api_key=api_key,
        settings=current,
    )
    try:
        result = await provider.complete(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            max_output_tokens=current.minimax_max_output_tokens,
            temperature=0.3,
            n=1,
            tools=None,
            tool_choice=None,
            response_format=None,
        )
    except Exception as exc:  # noqa: BLE001 - toutes les causes mènent au 502
        raise ProviderError(
            exc.code if isinstance(exc, ProviderError) else "provider_error",
            f"Provider request failed: {exc.__class__.__name__}",
        ) from exc
    finally:
        await provider.close()

    content = result.content
    if not content or not content.strip():
        raise ProviderError(
            "provider_protocol_error",
            "Provider returned an empty or blank answer",
        )
    return content.strip()