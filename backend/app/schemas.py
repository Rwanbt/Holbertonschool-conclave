"""Schémas Pydantic des tuyaux HTTP (Palier 1-3).

Deux usages distincts pour chaque route :
- les modèles *Request valident l'ENTRÉE : ce que le client peut envoyer.
- les modèles *Response documentent la SORTIE : ce que le serveur renvoie.
Le Palier 3 ajoute la boucle agent (`/api/p3/agent`) : `AgentRequest`,
`ToolTraceEntry`, `ExecutionUsage` et `AgentResponse`.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_LENGTH = 12_000
MAX_DOCUMENT_LENGTH = 12_000

RecommendationText = Annotated[str, Field(min_length=1, max_length=500)]
VerdictListText = Annotated[str, Field(min_length=1, max_length=500)]
UnavailableToolText = Annotated[str, Field(min_length=1, max_length=120)]


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
    llm_rounds: int = Field(
        ..., ge=0, description="Nombre d'appels MiniMax effectués (0 = aucun)."
    )


class AgentResponse(BaseModel):
    answer: str = Field(..., description="Réponse finale de l'agent, en français.")
    model: str = Field(..., description="Nom exact du modèle appelé.")
    trace: list[ToolTraceEntry] = Field(
        ..., description="Trace chronologique de l'exécution des outils."
    )
    usage: ExecutionUsage = Field(..., description="Consommation et coût de l'exécution.")


# ---------------------------------------------------------------------------
# Palier 4 — analyse persistante, experts, arbitre, outils, événements
# ---------------------------------------------------------------------------

AnalysisStatus = Literal[
    "queued", "running", "completed", "degraded", "failed", "interrupted"
]
ExpertStatus = Literal["pending", "running", "completed", "error", "timeout"]
ExpertRole = Literal["avocat", "procureur", "comptable"]
ResponseRole = Literal["avocat", "procureur", "comptable", "arbitre"]

ToolName = Literal[
    "measure_current_document",
    "find_security_indicators_in_current_document",
    "estimate_current_analysis_cost",
]


class AnalysisCreateRequest(BaseModel):
    document: str = Field(
        ...,
        min_length=1,
        max_length=MAX_DOCUMENT_LENGTH,
        description="Document texte de 1 à 12 000 caractères.",
    )

    _document_not_blank = field_validator("document")(_non_blank)


class ToolConfiguration(BaseModel):
    """Configuration des outils figée pour une analyse donnée, au moment de
    sa création. Immuable ensuite : une modification du registre global
    (`/api/tool-commands`) n'affecte jamais une analyse déjà créée."""

    enabled_tools: list[ToolName] = Field(
        ..., description="Outils activés au moment de la création de l'analyse."
    )
    disabled_tools: list[ToolName] = Field(
        ..., description="Outils désactivés au moment de la création de l'analyse."
    )


class SecurityReport(BaseModel):
    """Ce que le serveur a repéré dans le document soumis.

    `prompt_injection_suspected` est un signal d'OBSERVABILITÉ, jamais une
    autorisation : l'analyse se déroule normalement, les défenses réelles
    étant structurelles (outils figés côté serveur, outils sans argument,
    sortie validée par schéma). Voir SECURITY.md.
    """

    prompt_injection_suspected: bool = Field(
        ..., description="Vrai si une tournure d'instruction a été repérée."
    )
    signals: list[str] = Field(
        default_factory=list,
        description="Noms des motifs repérés (heuristique, bornée à 10).",
    )


class AnalysisCreated(BaseModel):
    analysis_id: str = Field(..., description="Identifiant unique UUID de l'analyse.")
    status: AnalysisStatus = Field(..., description="Statut initial de l'analyse (queued).")
    created_at: str = Field(..., description="Date de création ISO-8601 UTC.")
    tool_configuration: ToolConfiguration = Field(
        ..., description="Configuration des outils figée pour cette analyse."
    )
    security: SecurityReport = Field(
        ..., description="Signaux repérés dans le document soumis."
    )


class StartAnalysisResponse(BaseModel):
    analysis_id: str = Field(..., description="Identifiant de l'analyse.")
    status: AnalysisStatus = Field(..., description="Statut après tentative de démarrage.")
    already_started: bool = Field(
        ..., description="True si l'analyse était déjà running ou terminale."
    )


class Finding(BaseModel):
    title: str = Field(..., min_length=1, max_length=160, description="Intitulé court du constat.")
    evidence: str = Field(..., min_length=1, max_length=800, description="Preuve localisée dans le document.")
    impact: str = Field(..., min_length=1, max_length=600, description="Impact potentiel.")
    priority: Literal["low", "medium", "high"] = Field(
        ..., description="Priorité du constat."
    )


class AgentOutput(BaseModel):
    role: ExpertRole = Field(..., description="Rôle de l'expert qui a produit la sortie.")
    summary: str = Field(..., min_length=1, max_length=1200, description="Synthèse argumentée.")
    findings: list[Finding] = Field(
        ..., min_length=2, max_length=5, description="De 2 à 5 constats structurés."
    )
    score_label: str = Field(..., min_length=1, max_length=80, description="Libellé humain de la note.")
    score: int = Field(..., ge=0, le=100, description="Note sur 100.")
    recommendations: list[RecommendationText] = Field(
        [], max_length=3, description="Jusqu'à 3 recommandations."
    )
    unavailable_tools: list[UnavailableToolText] = Field(
        [], max_length=10, description="Outils indisponibles constatés, bornés en taille."
    )


class ArbiterVerdict(BaseModel):
    decision: Literal["go", "go_with_conditions", "no_go"] = Field(
        ..., description="Décision finale de l'Arbitre."
    )
    score: int = Field(..., ge=0, le=100, description="Score global sur 100.")
    main_disagreement: str = Field(
        ..., min_length=1, max_length=1000, description="Désaccord principal entre les experts."
    )
    priority_risks: list[VerdictListText] = Field(
        [], max_length=3, description="Jusqu'à 3 risques prioritaires."
    )
    actions: list[VerdictListText] = Field(
        [], max_length=3, description="Jusqu'à 3 actions ordonnées."
    )
    accepted_tradeoff: str = Field(
        ..., min_length=1, max_length=1000, description="Compromis accepté."
    )
    unavailable_agents: list[ExpertRole] = Field(
        [], description="Experts absents au moment du verdict."
    )


class ExpertRunView(BaseModel):
    role: ExpertRole = Field(..., description="Rôle de l'expert.")
    status: ExpertStatus = Field(..., description="Statut terminal ou courant du run.")
    output: AgentOutput | None = Field(
        None, description="Sortie validée, ou null si échec/timeout."
    )
    error_code: str | None = Field(
        None, description="Code d'erreur court (expert_timeout, structured_output_error…)."
    )


class AnalysisSnapshot(BaseModel):
    analysis_id: str = Field(..., description="Identifiant de l'analyse.")
    document: str = Field(..., description="Document soumis (jamais journalisé, mais exposé au front).")
    status: AnalysisStatus = Field(..., description="Statut courant de l'analyse.")
    created_at: str = Field(..., description="Date de création ISO-8601 UTC.")
    started_at: str | None = Field(None, description="Date de démarrage des experts.")
    completed_at: str | None = Field(None, description="Date de fin terminale.")
    error_code: str | None = Field(None, description="Code d'arrêt contrôlé éventuel.")
    avocat: ExpertRunView = Field(..., description="Run de l'Avocat.")
    procureur: ExpertRunView = Field(..., description="Run du Procureur.")
    comptable: ExpertRunView = Field(..., description="Run du Comptable.")
    verdict: ArbiterVerdict | None = Field(None, description="Verdict validé, ou null.")
    usage: ExecutionUsage = Field(..., description="Usage agrégé de l'analyse.")
    guardrails: dict[str, Any] = Field(
        ..., description="Limites appliquées (timeouts, rounds, statuts autorisés)."
    )
    tool_configuration: ToolConfiguration = Field(
        ..., description="Configuration des outils figée pour cette analyse."
    )
    security: SecurityReport = Field(
        ..., description="Signaux repérés dans le document soumis."
    )


class ToolState(BaseModel):
    tool_name: ToolName = Field(..., description="Nom exact de l'outil.")
    enabled: bool = Field(..., description="État courant (source de vérité : SQLite).")
    description: str = Field(..., description="Description lue par le modèle.")


class ToolCatalogResponse(BaseModel):
    tools: list[ToolState] = Field(..., description="Les trois outils et leur état.")


class ToolCommandRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Commande /tools à exécuter.")


class ToolCommandResponse(BaseModel):
    action: Literal["list", "enable", "disable"] = Field(
        ..., description="Action appliquée par la commande."
    )
    message: str = Field(..., description="Message humain court décrivant le résultat.")
    tool_name: ToolName | None = Field(
        None,
        description="Outil concerné (enable/disable), null pour une simple liste.",
    )
    enabled: bool | None = Field(
        None,
        description="État après application (enable/disable), null pour une simple liste.",
    )
    tools: list[ToolState] = Field(
        default_factory=list,
        description="Catalogue complet des outils et de leurs états persistés.",
    )


class AgentResponseStarted(BaseModel):
    analysis_id: str = Field(..., description="Identifiant de l'analyse.")
    role: ResponseRole = Field(..., description="Rôle dont la réponse commence à défiler.")


class AgentResponseDelta(BaseModel):
    analysis_id: str = Field(..., description="Identifiant de l'analyse.")
    role: ResponseRole = Field(..., description="Rôle qui diffuse le texte live.")
    sequence: int = Field(..., ge=1, description="Numéro strictement croissant par rôle.")
    delta: str = Field(..., min_length=1, description="Fragment de texte live, borné en taille.")


class AgentResponseCompleted(BaseModel):
    analysis_id: str = Field(..., description="Identifiant de l'analyse.")
    role: ResponseRole = Field(..., description="Rôle dont la sortie a été validée.")


class AgentResponseFailed(BaseModel):
    analysis_id: str = Field(..., description="Identifiant de l'analyse.")
    role: ResponseRole = Field(..., description="Rôle dont la réponse a échoué.")
    error_code: str = Field(
        ..., min_length=1, description="Code d'échec (protocol_error, structured_output_error…)."
    )
    error_detail: str | None = Field(
        None, max_length=120, description="Sous-cause technique bornée, sans contenu utilisateur."
    )


class ToolEventData(BaseModel):
    analysis_id: str = Field(..., description="Identifiant de l'analyse.")
    agent_role: str = Field(..., description="Rôle qui a déclenché l'outil.")
    llm_round: int = Field(..., ge=1, description="Tour de boucle MiniMax concerné.")
    sequence: int = Field(..., ge=1, description="Ordre d'exécution de l'outil.")
    tool_name: ToolName = Field(..., description="Nom exact de l'outil.")
    status: Literal["success", "error"] = Field(..., description="Résultat de l'exécution.")
    input_summary: dict[str, Any] = Field(
        ..., description="Résumé borné des arguments, jamais le document."
    )
    output_summary: dict[str, Any] | None = Field(
        None, description="Résumé borné du résultat, ou null en cas d'erreur."
    )
    duration_ms: int = Field(..., ge=0, description="Durée d'exécution mesurée.")
    error_code: str | None = Field(None, description="Code d'erreur court, ou null.")


class AnalysisEventEnvelope(BaseModel):
    """Un événement rejoué tel qu'il apparaît dans l'historique JSON paginé."""

    id: int = Field(..., ge=1, description="Identifiant strictement croissant.")
    event_type: str = Field(..., min_length=1, description="Type d'événement.")
    payload: dict[str, Any] = Field(..., description="Charge utile bornée, sans document.")
    created_at: str = Field(..., description="Date ISO-8601 UTC de l'événement.")


class EventsHistoryResponse(BaseModel):
    events: list[AnalysisEventEnvelope] = Field(
        ..., description="Événements strictement ordonnés par id, après `after`."
    )
    last_event_id: int = Field(
        ..., ge=0, description="Plus grand id renvoyé (0 si `events` est vide)."
    )
    has_more: bool = Field(
        ..., description="True si `limit` a tronqué le résultat : paginer avec `after`."
    )
