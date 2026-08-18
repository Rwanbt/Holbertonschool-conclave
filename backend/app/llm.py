"""Passerelle MiniMax — client OpenAI-compatible asynchrone.

MiniMax expose une API compatible OpenAI : le SDK `openai` fonctionne tel
quel en pointant `base_url` vers `https://api.minimax.io/v1`. Le seul
paramètre spécifique MiniMax utilisé ici — désactivation du raisonnement
approfondi pour ce test rapide — est isolé dans ce module via `extra_body`
(le SDK OpenAI ne connaît pas la clé `thinking`).

Toute défaillance (réseau, timeout, statut HTTP d'erreur, réponse vide ou
illisible) est traduite en `ProviderError` : la route décide ensuite du
code HTTP à renvoyer. Rien de la réponse brute ni des en-têtes fournisseur
n'est exposé ici au-delà de l'`answer` nettoyée.
"""

from typing import Final

from openai import AsyncOpenAI

from .config import Settings, get_settings

SYSTEM_PROMPT: Final[str] = (
    "Tu es une passerelle de test du backend CONCLAVE. "
    "Réponds en français, en une ou deux phrases courtes, "
    "sans titre, sans liste ni Markdown complexe."
)


class ProviderError(RuntimeError):
    """Le fournisseur MiniMax est indisponible ou sa réponse est inexploitable."""


def build_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.minimax_api_key,
        base_url=settings.minimax_base_url,
        timeout=30.0,
        max_retries=1,
    )


async def generate_answer(message: str, settings: Settings | None = None) -> str:
    current = settings if settings is not None else get_settings()

    try:
        async with build_client(current) as client:
            completion = await client.chat.completions.create(
                model=current.minimax_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ],
                max_completion_tokens=current.minimax_max_output_tokens,
                temperature=0.3,
                n=1,
                extra_body={"thinking": {"type": "disabled"}},
            )
    except Exception as exc:  # noqa: BLE001 - toutes les causes mènent au 502
        raise ProviderError(f"MiniMax request failed: {exc.__class__.__name__}") from exc

    if not completion.choices:
        raise ProviderError("MiniMax returned no choices")

    content = completion.choices[0].message.content
    if not content or not content.strip():
        raise ProviderError("MiniMax returned an empty or blank answer")

    return content.strip()
