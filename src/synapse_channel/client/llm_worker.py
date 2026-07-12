# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — on-channel worker that answers with a chat backend
"""On-channel worker that turns inbound messages into model replies.

:class:`SynapseLLMWorker` joins the hub as a named agent, watches the channel,
and replies through a :mod:`~synapse_channel.client.chat_backends` backend when a
message is addressed to it (by target, by mention, or from ``USER``). It keeps a
short rolling transcript for context, throttles its own output, and filters out
system and sidecar noise so it never answers itself or coordination chatter.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import deque
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.client.chat_backends import (
    ChatBackend,
    OpenAIChatClient,
    RuleBasedClient,
    sanitize_text,
)
from synapse_channel.core.capability_card_signing import (
    DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
)
from synapse_channel.core.protocol import MessageType

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"

SYSTEM_PROMPT = (
    "You are an engineering teammate in a multi-agent Synapse chat. "
    "Give short, direct, technically useful replies. No fluff."
)

_SERVICE_MESSAGE_TYPES = frozenset(
    {
        MessageType.PRESENCE_UPDATE,
        MessageType.WELCOME,
        MessageType.WHO_SNAPSHOT,
        MessageType.STATE_SNAPSHOT,
        MessageType.HISTORY_SNAPSHOT,
        MessageType.RESOURCE_OFFERED,
        MessageType.TASK_UPDATED,
        MessageType.CLAIM_GRANTED,
        MessageType.CLAIM_DENIED,
    }
)
_SERVICE_PAYLOAD_PREFIXES = ("[ACK", "[ROUTE]", "[MAIN]")


def is_service_message(sender: str, payload: str, msg_type: str = "chat") -> bool:
    """Return whether a message is system/sidecar noise the worker should skip.

    Parameters
    ----------
    sender : str
        Name of the message sender.
    payload : str
        Message text.
    msg_type : str, optional
        Message type. Defaults to ``"chat"``.

    Returns
    -------
    bool
        ``True`` for hub messages, ``*_LITE``/``*_CORE`` sidecars, system
        snapshot/notification types, and ``[ACK]``/``[ROUTE]``/``[MAIN]`` relay
        markers; ``False`` otherwise.
    """
    if sender == "SynapseHub":
        return True
    if sender.endswith("_LITE") or sender.endswith("_CORE"):
        return True
    if msg_type in _SERVICE_MESSAGE_TYPES:
        return True
    text = payload.lstrip()
    return text.startswith(_SERVICE_PAYLOAD_PREFIXES)


class SynapseLLMWorker:
    """A hub agent that answers addressed messages via a chat backend.

    Parameters
    ----------
    name : str
        Agent name presented on the channel.
    uri : str, optional
        Hub URI. Defaults to :data:`~synapse_channel.client.agent.DEFAULT_HUB_URI`.
    provider : str, optional
        Backend provider: ``ollama`` (default), ``openai``, or ``rule``.
    model : str, optional
        Model identifier for HTTP providers. Defaults to ``"llama3"``.
    base_url : str, optional
        OpenAI-compatible base URL. Defaults to the local Ollama endpoint.
    api_key_env : str, optional
        Environment variable holding the API key. Defaults to ``"OPENAI_API_KEY"``.
    max_context : int, optional
        Number of recent messages retained for prompt context (floored at 2).
    reply_target_mode : str, optional
        ``"all"`` to answer the room or ``"sender"`` to answer privately.
    min_reply_interval : float, optional
        Minimum seconds between replies (floored at 0). Defaults to ``0.7``.
    ready_timeout : float, optional
        Seconds to wait for the hub handshake in :meth:`run`. Defaults to ``5.0``.
    token : str or None, optional
        Shared-secret token presented to a hub that requires authentication;
        ``None`` for an open hub.
    task_classes : tuple[str, ...] or list[str], optional
        Routing classes this worker advertises on its capability card; defaults
        to ``("chat",)``.
    heavy_model : str, optional
        Model used for the ``heavy`` tier when ``provider="tiered"``; defaults to
        ``model`` when empty.
    capability_card_key_path, capability_card_key_id, capability_card_project : str, optional
        Separate Ed25519 card-signing credential and project binding. All are
        opt-in; unsigned advisory discovery remains the default.
    capability_card_lifetime_seconds : float, optional
        Lifetime recorded in each signed advertisement.
    """

    def __init__(
        self,
        *,
        name: str,
        uri: str = DEFAULT_HUB_URI,
        provider: str = "ollama",
        model: str = "llama3",
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        api_key_env: str = "OPENAI_API_KEY",
        max_context: int = 8,
        reply_target_mode: str = "all",
        min_reply_interval: float = 0.7,
        ready_timeout: float = 5.0,
        token: str | None = None,
        task_classes: tuple[str, ...] | list[str] = ("chat",),
        heavy_model: str = "",
        capability_card_key_path: str | None = None,
        capability_card_key_id: str = "",
        capability_card_project: str = "",
        capability_card_lifetime_seconds: float = DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
    ) -> None:
        self.name = name
        self.uri = uri
        self.provider = provider
        self.model = model
        self.heavy_model = heavy_model or model
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.reply_target_mode = reply_target_mode
        self.min_reply_interval = max(float(min_reply_interval), 0.0)
        self.ready_timeout = max(float(ready_timeout), 0.1)
        self.task_classes = tuple(task_classes)
        self.context: deque[tuple[str, str]] = deque(maxlen=max(max_context, 2))
        self.inbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self.last_reply_ts = 0.0

        self.agent = SynapseAgent(
            self.name,
            on_message_callback=self.on_message,
            uri=self.uri,
            token=token,
            capability_card_key_path=capability_card_key_path,
            capability_card_key_id=capability_card_key_id,
            capability_card_project=capability_card_project,
            capability_card_lifetime_seconds=capability_card_lifetime_seconds,
        )
        self.client: ChatBackend = self._build_client()

    def _build_client(self) -> ChatBackend:
        """Construct the chat backend named by ``provider``.

        Returns
        -------
        ChatBackend
            A :class:`RuleBasedClient`, :class:`OpenAIChatClient`, or a
            :class:`~synapse_channel.client.routing.TieredChatClient` for ``tiered``.

        Raises
        ------
        RuntimeError
            If ``provider`` is not ``rule``, ``openai``, ``ollama``, or ``tiered``.
        """
        if self.provider == "rule":
            return RuleBasedClient()
        if self.provider in ("openai", "ollama"):
            return self._http_client(self.model)
        if self.provider == "tiered":
            return self._build_tiered_client()
        raise RuntimeError(
            f"Unsupported provider '{self.provider}'. Use openai, ollama, rule, or tiered."
        )

    def _http_client(self, model: str) -> OpenAIChatClient:
        """Build an OpenAI-compatible HTTP client for ``model``."""
        api_key = os.getenv(self.api_key_env, "").strip() or "ollama"
        effective_base = self.base_url
        # A local Ollama preset left on the OpenAI default is redirected to the
        # local server, which speaks the same /v1 protocol.
        if self.provider in ("ollama", "tiered") and self.base_url == OPENAI_DEFAULT_BASE_URL:
            effective_base = DEFAULT_OLLAMA_BASE_URL
        return OpenAIChatClient(
            api_key=api_key, model=model, base_url=effective_base, timeout_seconds=60.0
        )

    def _build_tiered_client(self) -> ChatBackend:
        """Build a tiered backend: a rule path plus SLM and heavy HTTP models."""
        from synapse_channel.client.routing import TaskClass, TieredChatClient

        return TieredChatClient(
            {
                TaskClass.RULE: RuleBasedClient(),
                TaskClass.SLM: self._http_client(self.model),
                TaskClass.HEAVY: self._http_client(self.heavy_model),
            }
        )

    async def on_message(self, data: dict[str, Any]) -> None:
        """Filter an inbound message and queue it when a reply is warranted.

        Parameters
        ----------
        data : dict[str, Any]
            A decoded inbound message envelope.
        """
        if str(data.get("type", MessageType.CHAT)) != MessageType.CHAT:
            return
        sender = str(data.get("sender", "?"))
        payload = str(data.get("payload", ""))

        if sender == self.name:
            return
        if is_service_message(sender, payload, MessageType.CHAT):
            return
        if not payload.strip():
            return

        self.context.append((sender, sanitize_text(payload, max_len=260)))
        target = str(data.get("target", "all"))
        if self._should_reply(sender=sender, payload=payload, target=target):
            await self.inbox.put({"sender": sender, "payload": payload})

    def _should_reply(self, *, sender: str, payload: str, target: str) -> bool:
        """Decide whether a message warrants a reply from this worker.

        Parameters
        ----------
        sender : str
            Message sender.
        payload : str
            Message text.
        target : str
            Declared recipient of the message.

        Returns
        -------
        bool
            ``True`` when addressed directly, mentioned by name, or sent by
            ``USER``; ``False`` otherwise.
        """
        if sender == self.name:
            return False
        if target == self.name:
            return True
        if self.name.lower() in payload.lower():
            return True
        return sender == "USER"

    def _build_user_prompt(self, sender: str, payload: str) -> str:
        """Render the recent transcript and latest message into a prompt."""
        recent = list(self.context)[-6:]
        lines_text = "\n".join(f"{s}: {p}" for s, p in recent)
        return (
            "Conversation excerpt:\n"
            f"{lines_text}\n\n"
            f"Latest from {sender}: {payload}\n\n"
            "Reply briefly, concretely, and action-oriented for team engineering coordination."
        )

    async def _process_item(self, item: dict[str, str]) -> None:
        """Throttle, generate, and send a reply for one queued message.

        Parameters
        ----------
        item : dict[str, str]
            A ``{"sender", "payload"}`` work item from the inbox.
        """
        gap = time.time() - self.last_reply_ts
        if gap < self.min_reply_interval:
            await asyncio.sleep(self.min_reply_interval - gap)

        sender = item["sender"]
        payload = item["payload"]
        prompt = self._build_user_prompt(sender, payload)
        try:
            text = await asyncio.to_thread(
                self.client.generate, system_prompt=SYSTEM_PROMPT, user_prompt=prompt
            )
            text = sanitize_text(text, max_len=420)
        except Exception as exc:
            text = f"{self.name}: model error -> {exc}"

        target = sender if self.reply_target_mode == "sender" else "all"
        await self.agent.chat(text, target=target)
        self.last_reply_ts = time.time()

    async def _advertise(self) -> None:
        """Advertise this worker's capability card to the hub."""
        await self.agent.advertise(
            description=f"{self.provider} chat worker",
            skills=(self.provider,),
            task_classes=self.task_classes,
            model=self.model,
        )

    async def _worker_loop(self) -> None:
        """Process queued work items until the agent stops running."""
        while self.agent.running:
            item = await self.inbox.get()
            await self._process_item(item)

    async def run(self) -> None:
        """Connect, wait for the handshake, and run the worker loop.

        The connection and worker tasks run concurrently; when either finishes
        the other is cancelled and any terminal error is reported.
        """
        conn_task = asyncio.create_task(self.agent.connect())
        ready = await self.agent.wait_until_ready(timeout=self.ready_timeout)
        if not ready:
            print(f"[{self.name}] Warning: handshake timeout.")
        else:
            await self._advertise()
        worker_task = asyncio.create_task(self._worker_loop())
        done, pending = await asyncio.wait(
            {conn_task, worker_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            # Await the cancellation so the survivor's cleanup runs before this
            # coroutine returns — a merely-scheduled cancel would leak a pending
            # task into event-loop shutdown.
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            try:
                task.result()
            except Exception as exc:
                print(f"[{self.name}] worker stopped: {exc}")
