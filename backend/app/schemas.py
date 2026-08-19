"""Schémas Pydantic des tuyaux HTTP (Palier 1-3).

Deux usages distincts pour chaque route :
- les modèles *Request valident l'ENTRÉE : ce que le client peut envoyer.
- les modèles *Response documentent la SORTIE : ce que le serveur renvoie.
Le Palier 3 ajoute la boucle agent (`/api/p3/agent`) : `AgentRequest`,
`ToolTraceEntry`, `ExecutionUsage` et `AgentResponse`.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_LENGTH = 12_000
MAX_DOCUMENT_LENGTH = 12_000


class LLMRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="Texte français à faire traiter par le modèle.",
    )

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must contain at least one non-space character")
        return value


class LLMResponse(BaseModel):
    answer: str = Field(..., description="Réponse du modèle, texte court en français.")
    model: str = Field(..., description="Nom exact du modèle appelé.")


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain at least one non-space character")
    return value


class AgentRequest(BaseModel):
    instruction: str = Field(
        ...,
        min_length=1,
        max_length=MAX_DOCUMENT_LENGTH,
        description="Intention exprimée en français, traitable étape par étape.",
    )
    document: str = Field(
        ...,
        min_length=1,
        max_length=MAX_DOCUMENT_LENGTH,
        description="Document analysé. Reste côté serveur, jamais envoyé au modèle.",
    )

    _instruction_not_blank = field_validator("instruction")(_non_blank)
    _document_not_blank = field_validator("document")(_non_blank)


class ToolTraceEntry(BaseModel):
    sequence: int = Field(..., ge=1, description="Ordre d'exécution, depuis 1.")
    tool_name: str = Field(..., description="Nom exact de l'outil appelé.")
    status: Literal["success", "error"] = Field(
        ..., description="Résultat de l'exécution de l'outil."
    )
    input_summary: dict[str, Any] = Field(
        ...,
        description="Résumé borné des arguments. Jamais le document ni une clé.",
    )
    output_summary: dict[str, Any] | None = Field(
        None, description="Résumé borné du résultat, ou null en cas d'erreur."
    )
    duration_ms: int = Field(..., ge=0, description="Durée d'exécution mesurée.")
    error_code: str | None = Field(
        None,
        description="Code d'erreur court (tool_disabled, unknown_tool, …), null si succès.",
    )


class ExecutionUsage(BaseModel):
    input_tokens: int | None = Field(
        None, ge=0, description="Total des jetons d'entrée consommés, ou null."
    )
    output_tokens: int | None = Field(
        None, ge=0, description="Total des jetons de sortie consommés, ou null."
    )
    total_tokens: int | None = Field(
        None, ge=0, description="Total des jetons consommés, ou null."
    )
    estimated_cost_usd: float | None = Field(
        None,
        ge=0.0,
        description="Coût estimatif (USD), calculé avec les tarifs configurés, ou null.",
    )
    total_latency_ms: int = Field(
        ..., ge=0, description="Latence cumulée des appels MiniMax, en millisecondes."
    )
    llm_rounds: int = Field(..., ge=1, description="Nombre d'appels MiniMax effectués.")


class AgentResponse(BaseModel):
    answer: str = Field(..., description="Réponse finale de l'agent, en français.")
    model: str = Field(..., description="Nom exact du modèle appelé.")
    trace: list[ToolTraceEntry] = Field(
        ..., description="Trace chronologique de l'exécution des outils."
    )
    usage: ExecutionUsage = Field(..., description="Consommation et coût de l'exécution.")
