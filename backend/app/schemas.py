"""Schémas Pydantic du tuyau HTTP minimal (Palier 1-2).

Deux usages distincts :
- `LLMRequest` valide l'ENTRÉE : ce que le client peut envoyer au serveur.
- `LLMResponse` documente la SORTIE : ce que le serveur renvoie au client.
"""

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_LENGTH = 12_000


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
