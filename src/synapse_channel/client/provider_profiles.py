# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicit HTTP profiles for model providers
"""Provider endpoints and wire families; profiles are not live support claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WireFamily = Literal["openai", "anthropic", "google"]


@dataclass(frozen=True)
class ProviderProfile:
    """One documented API surface with a conservative default endpoint."""

    name: str
    base_url: str
    family: WireFamily
    key_env: str
    paid: bool = True


PROFILES: dict[str, ProviderProfile] = {
    "ollama": ProviderProfile("ollama", "http://localhost:11434/v1", "openai", "", False),
    "openai": ProviderProfile("openai", "https://api.openai.com/v1", "openai", "OPENAI_API_KEY"),
    "deepseek": ProviderProfile(
        "deepseek", "https://api.deepseek.com", "openai", "DEEPSEEK_API_KEY"
    ),
    "openrouter": ProviderProfile(
        "openrouter", "https://openrouter.ai/api/v1", "openai", "OPENROUTER_API_KEY"
    ),
    "qwen": ProviderProfile(
        "qwen",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "openai",
        "DASHSCOPE_API_KEY",
    ),
    "mistral": ProviderProfile("mistral", "https://api.mistral.ai/v1", "openai", "MISTRAL_API_KEY"),
    "anthropic": ProviderProfile(
        "anthropic", "https://api.anthropic.com/v1", "anthropic", "ANTHROPIC_API_KEY"
    ),
    "google": ProviderProfile(
        "google", "https://generativelanguage.googleapis.com/v1beta", "google", "GEMINI_API_KEY"
    ),
    "xai": ProviderProfile("xai", "https://api.x.ai/v1", "openai", "XAI_API_KEY"),
    "moonshot": ProviderProfile(
        "moonshot", "https://api.moonshot.ai/v1", "openai", "MOONSHOT_API_KEY"
    ),
}


def provider_profile(name: str) -> ProviderProfile:
    """Return a documented profile or reject an unknown provider explicitly."""
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown model provider: {name}") from exc
