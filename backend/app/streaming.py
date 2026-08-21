"""Streaming natif MiniMax-M3 (carte bonus du Palier 4).

MiniMax expose une API OpenAI-compatible : `stream=True` + le SDK `openai`
fonctionnent tels quels. Deux particularités MiniMax gérées ici :

- `delta.content` peut être CUMULATIF (chaque morceau répète tout le texte déjà
  produit) au lieu d'un vrai delta : il faut `normalize_delta` pour ne jamais
  dupliquer (« Bonjour » → « BBoo… »).
- la réponse finale d'un expert/arbitre est une ENVELOPPE
  `<LIVE_RESPONSE>…</LIVE_RESPONSE><FINAL_JSON>{…}</FINAL_JSON>` parsée par un
  automate à états qui tolère les marqueurs coupés entre deux morceaux.

Le collecteur reconstruit, à partir des morceaux OpenAI, un objet
« `StreamedCompletion` » du même type (choices/usage) que la réponse
non-streamée : la boucle agent `run_agent_loop` peut donc traiter les deux
formes avec le même code.

Contrats respectés :
- le texte live n'est JAMAIS la sortie validée : seule la section `FINAL_JSON`
  est extraite puis validée par Pydantic ;
- le JSON final n'apparaît jamais dans les événements `agent.response.*` ;
- pas de `reasoning_content`/`<think>` diffusé : `thinking` est désactivé via
  `extra_body`, et l'enveloppe n'autorise pas de section de raisonnement ;
- le chunk final `choices=[]` + `usage` seul n'est pas jeté : il nourrit le
  compteur agrégé exactement une fois ;
- un brouillon live est plafonné (`stream_max_draft_chars`) et émis par
  paquets bornés (`stream_delta_batch_chars`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .config import Settings

LIVE_OPEN = "<LIVE_RESPONSE>"
LIVE_CLOSE = "</LIVE_RESPONSE>"
JSON_OPEN = "<FINAL_JSON>"
JSON_CLOSE = "</FINAL_JSON>"

LiveSink = Callable[[str, dict[str, Any]], Awaitable[None]]
"""Sink des événements de réponse live : `(kind, fields)` avec kind in
{"agent.response.started", "agent.response.delta"} et fields
{"role", …} / {"role", "delta", …}. La numérotation des séquences et la
persistance SQLite sont gérées par la fermeture de l'appelant (experts.py).
"""


class LiveSinkError(RuntimeError):
    """La diffusion/persistance locale a échoué, pas le fournisseur LLM."""


def normalize_delta(buffer: str, incoming: str) -> str:
    """Calcule le vrai delta entre l'accumulé et un morceau reçu.

    MiniMax peut envoyer des morceaux cumulatifs (chaque morceau répète tout
    le texte déjà produit) ou des préfixes strictement répétés (un morceau
    déjà entièrement contenu au début de l'accumulé). Seuls ces deux cas sont
    traités sans jamais recopier un texte déjà présent :

    - incoming commence par buffer  -> cumulatif, nouveau = suffixe ;
    - buffer commence par incoming  -> préfixe répété, rien de nouveau ;
    - sinon                         -> delta classique, même s'il répète un
      caractère, un espace ou une sous-chaîne déjà vus ailleurs dans le
      texte : une règle générale `buffer.endswith(incoming)` supprimerait à
      tort une répétition légitime (p.ex. deux espaces consécutifs).
    """
    if not incoming:
        return ""
    if incoming.startswith(buffer):
        return incoming[len(buffer):]
    if buffer.startswith(incoming):
        return ""
    return incoming


@dataclass
class StreamedFunction:
    name: str = ""
    arguments: str = ""


@dataclass
class StreamedToolCall:
    id: str = ""
    function: StreamedFunction = field(default_factory=StreamedFunction)


@dataclass
class StreamedMessage:
    content: str | None = None
    tool_calls: list[StreamedToolCall] | None = None


@dataclass
class StreamedChoice:
    message: StreamedMessage
    finish_reason: str | None = None


@dataclass
class StreamedUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class StreamedCompletion:
    choices: list[StreamedChoice]
    usage: StreamedUsage | None
    protocol_error: str | None = None
    final_json: str | None = None
    live_text: str = ""


class ToolCallAssembler:
    """Assemble les appels d'outil fragmentés (ou cumulatifs) d'un index."""

    def __init__(self, index: int):
        self.index = index
        self.id = ""
        self.name = ""
        self.arguments = ""

    def feed(self, call_id: str | None, name: str | None, arguments: str | None) -> None:
        if call_id:
            self.id += normalize_delta(self.id, call_id)
        if name:
            self.name += normalize_delta(self.name, name)
        if arguments:
            self.arguments += normalize_delta(self.arguments, arguments)

    def to_tool_call(self) -> StreamedToolCall:
        return StreamedToolCall(id=self.id, function=StreamedFunction(self.name, self.arguments))


class EnvelopeParser:
    """Automate à états pour l'enveloppe de réponse finale.

    États : await_live_marker -> inside_live -> await_final_marker ->
    inside_final_json -> done (ou error). Les marqueurs peuvent être coupés
    entre deux morceaux : un suffixe de longueur `len(marqueur) - 1` est
    conservé entre les appels. Les espaces autour des sections sont tolérés.
    Refus : deux sections live, marqueurs inversés, section finale absente,
    section `<FINAL_JSON>` sans `<LIVE_RESPONSE>` non vide (un tour final ne
    peut plus terminer avec zéro texte live).
    """

    def __init__(self, max_live_chars: int):
        self._max_live_chars = max_live_chars
        self._state = "await_live_marker"
        self._pending = ""
        self._live_text = ""
        self._final_json: str | None = None
        self._error: str | None = None
        self._saw_marker = False
        self._live_started = False

    @property
    def live_text(self) -> str:
        return self._live_text

    @property
    def final_json(self) -> str | None:
        return self._final_json

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def done(self) -> bool:
        return self._state == "done"

    @property
    def saw_marker(self) -> bool:
        return self._saw_marker

    @property
    def live_started(self) -> bool:
        """True dès que la section `<LIVE_RESPONSE>` a réellement commencé
        (marqueur ouvrant rencontré), pour émettre `agent.response.started`
        immédiatement plutôt qu'au premier paquet bufferisé."""
        return self._live_started

    def _fail(self, message: str) -> None:
        if self._error is None:
            self._error = message
        self._state = "error"
        self._pending = ""

    def _emit_live(self, text: str) -> str:
        if not text or self._error:
            return ""
        remaining = self._max_live_chars - len(self._live_text)
        if remaining <= 0:
            return ""
        piece = text[:remaining]
        self._live_text += piece
        return piece

    def feed(self, text: str) -> str:
        """Consomme un morceau de texte brut ; renvoie le texte live NOUVEAU."""
        emitted = ""
        if not text or self._state == "done" or self._state == "error":
            return emitted
        self._pending += text
        while True:
            if self._state == "await_live_marker":
                open_idx = self._pending.find(LIVE_OPEN)
                json_idx = self._pending.find(JSON_OPEN)
                if json_idx != -1 and (open_idx == -1 or json_idx < open_idx):
                    close_idx = self._pending.find(LIVE_CLOSE)
                    if close_idx != -1 and close_idx < json_idx:
                        self._fail("inverted_markers")
                        return emitted
                    self._pending = self._pending[json_idx + len(JSON_OPEN):]
                    self._saw_marker = True
                    self._state = "inside_final_json"
                    continue
                if open_idx != -1:
                    close_idx = self._pending.find(LIVE_CLOSE)
                    if (close_idx != -1 and close_idx < open_idx) or (
                        json_idx != -1 and json_idx < open_idx
                    ):
                        self._fail("inverted_markers")
                        return emitted
                    self._pending = self._pending[open_idx + len(LIVE_OPEN):]
                    self._saw_marker = True
                    self._live_started = True
                    self._state = "inside_live"
                    continue
                if LIVE_CLOSE in self._pending or JSON_OPEN in self._pending:
                    self._fail("inverted_markers")
                    return emitted
                self._pending = self._pending[-(len(LIVE_OPEN) - 1):]
                return emitted

            if self._state == "inside_live":
                close_idx = self._pending.find(LIVE_CLOSE)
                if close_idx != -1:
                    if LIVE_OPEN in self._pending[:close_idx]:
                        self._fail("duplicate_live_section")
                        return emitted
                    emitted += self._emit_live(self._pending[:close_idx])
                    self._pending = self._pending[close_idx + len(LIVE_CLOSE):]
                    self._state = "await_final_marker"
                    continue
                if LIVE_OPEN in self._pending[: -len(LIVE_CLOSE) + 1]:
                    self._fail("duplicate_live_section")
                    return emitted
                emitted += self._emit_live(self._pending[: -len(LIVE_CLOSE) + 1])
                self._pending = self._pending[-len(LIVE_CLOSE) + 1:]
                return emitted

            if self._state == "await_final_marker":
                stripped = self._pending.lstrip()
                if LIVE_OPEN in stripped or LIVE_CLOSE in stripped:
                    self._fail("duplicate_or_inverted_live_section")
                    return emitted
                open_idx = stripped.find(JSON_OPEN)
                if open_idx != -1:
                    if JSON_CLOSE in stripped[:open_idx]:
                        self._fail("inverted_final_json_markers")
                        return emitted
                    self._pending = stripped[open_idx + len(JSON_OPEN):]
                    self._saw_marker = True
                    self._state = "inside_final_json"
                    continue
                if len(stripped) >= len(JSON_OPEN) - 1:
                    self._pending = stripped[-len(JSON_OPEN) + 1:]
                else:
                    self._pending = stripped
                return emitted

            if self._state == "inside_final_json":
                close_idx = self._pending.find(JSON_CLOSE)
                if close_idx != -1:
                    if JSON_OPEN in self._pending[:close_idx]:
                        self._fail("duplicate_final_json_section")
                        return emitted
                    self._final_json = (self._final_json or "") + self._pending[:close_idx]
                    self._pending = ""
                    self._state = "done"
                    continue
                if JSON_OPEN in self._pending[: -len(JSON_CLOSE) + 1]:
                    self._fail("duplicate_final_json_section")
                    return emitted
                self._final_json = (self._final_json or "") + self._pending[: -len(JSON_CLOSE) + 1]
                self._pending = self._pending[-len(JSON_CLOSE) + 1:]
                return emitted

            return emitted

    def finish(self) -> str | None:
        """Clôt le flux ; renvoie une erreur de protocole si les marqueurs vus
        ne sont pas suivis d'une section finale valide. Une réponse sans aucune
        balise (round d'outil ou texte libre) n'est pas une erreur : c'est un
        cas où l'enveloppe n'a simplement pas été demandée. Un `<FINAL_JSON>`
        clos sans jamais avoir ouvert une section live non vide EST une erreur
        de protocole : un tour final ne peut plus terminer avec zéro texte
        live (une réponse JSON-only ne peut plus réussir silencieusement)."""
        if self._state == "done":
            if not self._live_started:
                self._fail("missing_live_response")
                return self._error
            if not self._live_text.strip():
                self._fail("empty_live_response")
                return self._error
            return None
        if self._state == "error":
            return self._error
        if not self._saw_marker:
            return None
        if self._state == "inside_live" or self._state == "await_final_marker":
            self._fail("missing_final_json")
        elif self._state == "inside_final_json":
            self._fail("missing_final_json_close")
        return self._error


class StreamCollector:
    """Reconstruit la réponse complète morceau par morceau.

    Diffuse le texte live par paquets bornés (`agent.response.started` puis
    `agent.response.delta`) et n'extrait le JSON final qu'à la clôture.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        live_sink: LiveSink | None = None,
        response_role: str | None = None,
    ):
        self._settings = settings
        self._live_sink = live_sink
        self._response_role = response_role
        self.content = ""
        self._tool_calls: dict[int, ToolCallAssembler] = {}
        self._tool_order: list[int] = []
        self.usage: StreamedUsage | None = None
        self.finish_reason: str | None = None
        self._parser = EnvelopeParser(max_live_chars=settings.stream_max_draft_chars)
        self._live_buffer = ""
        self._last_flush = time.monotonic()
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_task_error: BaseException | None = None
        self._started = False
        self.protocol_error: str | None = None
        self.final_json: str | None = None
        self.live_text = ""

    async def feed(self, chunk: Any) -> None:
        self._raise_flush_task_error()
        if getattr(chunk, "choices", None):
            choice = chunk.choices[0]
            delta = getattr(choice, "delta", None)
            raw = (getattr(delta, "content", None) or "") if delta is not None else ""
            if raw:
                piece = normalize_delta(self.content, raw)
                if piece:
                    self.content += piece
                    emitted = self._parser.feed(piece)
                    await self._maybe_emit_started()
                    if emitted:
                        self.live_text += emitted
                        self._live_buffer += emitted
                        await self._flush_size()
                        self._schedule_time_flush()
            reason = getattr(choice, "finish_reason", None)
            if reason:
                self.finish_reason = reason
            for tool_call in (getattr(delta, "tool_calls", None) or []) if delta is not None else []:
                self._feed_tool_call(tool_call)
        if getattr(chunk, "usage", None) is not None and self.usage is None:
            usage = chunk.usage
            self.usage = StreamedUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            )
        await self._flush_time()

    def _raise_flush_task_error(self) -> None:
        if self._flush_task_error is not None:
            error = self._flush_task_error
            self._flush_task_error = None
            raise error

    def _schedule_time_flush(self) -> None:
        if not self._live_buffer or self._flush_task is not None:
            return
        interval = self._settings.stream_flush_interval_ms / 1000.0
        self._flush_task = asyncio.create_task(self._delayed_flush(interval))

    async def _delayed_flush(self, interval: float) -> None:
        try:
            await asyncio.sleep(interval)
            if self._live_buffer:
                await self._flush()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - re-raised by feed/finish
            self._flush_task_error = exc
        finally:
            self._flush_task = None

    def _cancel_flush_task(self) -> None:
        task = self._flush_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._flush_task = None

    def _feed_tool_call(self, tool_call: Any) -> None:
        index = getattr(tool_call, "index", None)
        if index is None:
            return
        assembler = self._tool_calls.get(index)
        if assembler is None:
            assembler = ToolCallAssembler(index)
            self._tool_calls[index] = assembler
            self._tool_order.append(index)
        function = getattr(tool_call, "function", None)
        assembler.feed(
            call_id=getattr(tool_call, "id", None),
            name=getattr(function, "name", None) if function is not None else None,
            arguments=getattr(function, "arguments", None) if function is not None else None,
        )

    async def _maybe_emit_started(self) -> None:
        """Émet `agent.response.started` dès que `<LIVE_RESPONSE>` est
        reconnu, sans attendre qu'un paquet atteigne la taille de flush."""
        if self._started or not self._parser.live_started:
            return
        if self._live_sink is None or not self._response_role:
            return
        self._started = True
        await self._emit_live(
            "agent.response.started", {"role": self._response_role}
        )

    async def _emit_live(self, kind: str, fields: dict[str, Any]) -> None:
        if self._live_sink is None:
            return
        try:
            await self._live_sink(kind, fields)
        except Exception as exc:  # noqa: BLE001 - distinguée du fournisseur
            raise LiveSinkError(
                f"live event sink failed: {exc.__class__.__name__}"
            ) from exc

    async def _flush_size(self) -> None:
        if len(self._live_buffer) >= self._settings.stream_delta_batch_chars:
            await self._flush()

    async def _flush_time(self) -> None:
        interval = self._settings.stream_flush_interval_ms / 1000.0
        if self._live_buffer and (time.monotonic() - self._last_flush) >= interval:
            await self._flush()

    async def _flush(self) -> None:
        self._cancel_flush_task()
        if not self._live_buffer:
            self._last_flush = time.monotonic()
            return
        pending = self._live_buffer
        self._live_buffer = ""
        self._last_flush = time.monotonic()
        while pending:
            piece = pending[: self._settings.stream_delta_batch_chars]
            pending = pending[self._settings.stream_delta_batch_chars:]
            if self._live_sink is None or not self._response_role:
                continue
            if not self._started:
                self._started = True
                await self._emit_live(
                    "agent.response.started", {"role": self._response_role}
                )
            await self._emit_live(
                "agent.response.delta",
                {"role": self._response_role, "delta": piece},
            )

    async def finish(self) -> StreamedCompletion:
        self._raise_flush_task_error()
        self._cancel_flush_task()
        parser_error = self._parser.finish()
        self.final_json = self._parser.final_json
        await self._flush()
        # MiniMax-M3 peut accompagner un appel d'outil d'un texte public
        # balisé. Ce texte n'est pas une conclusion : la présence de
        # `tool_calls` fait foi et la boucle doit exécuter l'outil, puis
        # poursuivre au tour suivant. Rejeter cette réponse hybride bloquait
        # notamment le Comptable avant même sa mesure du document.
        # Les erreurs d'enveloppe ne concernent donc que les tours SANS outil.
        if not self._tool_calls and self.finish_reason == "length":
            self.protocol_error = "truncated_output"
        elif not self._tool_calls and parser_error:
            self.protocol_error = parser_error
        if self._tool_calls and self.protocol_error is None:
            for index in self._tool_order:
                assembler = self._tool_calls[index]
                if not assembler.id:
                    self.protocol_error = "invalid_tool_call_id"
                    break
                if not assembler.name:
                    self.protocol_error = "invalid_tool_call_name"
                    break
        tool_calls = (
            [self._tool_calls[index].to_tool_call() for index in self._tool_order]
            or None
        )
        return StreamedCompletion(
            choices=[StreamedChoice(StreamedMessage(content=self.content or None, tool_calls=tool_calls), self.finish_reason)],
            usage=self.usage,
            protocol_error=self.protocol_error,
            final_json=self.final_json,
            live_text=self.live_text,
        )


async def stream_chat_completion(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    max_completion_tokens: int,
    temperature: float,
    n: int,
    tools: list[dict[str, Any]],
    tool_choice: str | None,
    settings: Settings,
    live_sink: LiveSink | None = None,
    response_role: str | None = None,
) -> StreamedCompletion:
    """Appelle MiniMax en streaming et reconstitue la réponse complète."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
        "temperature": temperature,
        "n": n,
        "stream": True,
        "stream_options": {"include_usage": True},
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    # Certains fournisseurs OpenAI-compatibles refusent `tools=[]` ou
    # `tool_choice=null`. Une analyse où tous les switches sont désactivés
    # reste donc une requête de chat valide, sans paramètres d'outils.
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    stream = await client.chat.completions.create(**kwargs)
    collector = StreamCollector(settings, live_sink=live_sink, response_role=response_role)
    async for chunk in stream:
        await collector.feed(chunk)
    return await collector.finish()
