# Model provider matrix

The C07 real local Ollama probe on 2026-09-19 UTC listed eight models and
returned `OK.` from `gemma3:4b-ctx32k` through the new HTTP client, including
input and output token usage. No paid provider endpoint was contacted.

Core maintainers review official API and price changes at least weekly with
the vendor-discovery intake. A newly observed version or provider remains
unverified until its exact API and host contract is exercised.

Observed 2026-09-19 UTC. This matrix describes the `synapse worker` HTTP path. `Local` means a real localhost HTTP exchange against a protocol fixture; it does not mean the vendor service was contacted. `Unverified` means no authorised live call established vendor compatibility. `Unsupported` means this worker does not perform the feature. The separate OpenCode participant delegates model choice to OpenCode and does not establish any provider API claim here.

| Provider | Wire family | Auth | Model list | Stream parse | Tool calls | Usage | HTTP errors and rate headers | Cancellation | Live API |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ollama | OpenAI-compatible local | Local | Local | Local | Local parse; execution unsupported | Local | Local | Local request abort; server stop unverified | Local model path verified in C03 |
| OpenAI | Chat Completions | Local | Local | Local | Local parse; execution unsupported | Local | Local | Local request abort; server stop unverified | Unverified |
| DeepSeek | Chat Completions | Local | Unverified | Local family parser | Local family parser; execution unsupported | Local family parser | Local | Local request abort; server stop unverified | Unverified |
| OpenRouter | Chat Completions gateway | Local | Unverified | Local family parser | Local family parser; execution unsupported | Local family parser | Local | Local request abort; server stop unverified | Unverified |
| Qwen | OpenAI-compatible regional endpoint | Local | Unverified | Unverified model-specific delta semantics | Local family parser; execution unsupported | Local family parser | Local | Local request abort; server stop unverified | Unverified |
| Mistral | Chat Completions | Local | Unverified | Local family parser | Local family parser; execution unsupported | Local family parser | Local | Local request abort; server stop unverified | Unverified |
| Anthropic | Messages | Local | Local | Local | Local parse; execution unsupported | Local | Local | Local request abort; server stop unverified | Unverified |
| Google | GenerateContent | Local | Local | Local | Local parse; execution unsupported | Local | Local | Local request abort; server stop unverified | Unverified |
| xAI | Chat Completions | Local | Unverified | Local family parser | Local family parser; execution unsupported | Local family parser | Local | Local request abort; server stop unverified | Unverified |
| Moonshot | Chat Completions | Local | Unverified | Local family parser | Local family parser; execution unsupported | Local family parser | Local | Local request abort; server stop unverified | Unverified |

`ProviderHTTPClient` returns tool calls as data. It never runs tools, retries a turn, or executes model output. The synchronous worker asks for one non-stream completion; its lower-level client can request SSE and normalizes the buffered result. Cancellation checks before and during reads, bounded by the socket timeout; provider-side cancellation acknowledgement is unknown. Rate headers are returned in `ProviderReply`; the worker does not yet adapt its scheduling to them. A quoted API price never implies a current account quota.

The relevant documented surfaces are [OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create), [DeepSeek chat completion](https://api-docs.deepseek.com/api/create-chat-completion/), [OpenRouter chat](https://openrouter.ai/docs/quickstart), [Qwen compatible API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions), [Mistral chat completion](https://docs.mistral.ai/studio/conversations/chat-completion), [Anthropic Messages](https://platform.claude.com/docs/en/api/overview), [Gemini API](https://ai.google.dev/api), [xAI chat completion](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions), and [Moonshot API](https://platform.moonshot.ai/docs/guide/prompt-best-practice). Vendor endpoint and model availability can change; review the exact vendor documentation and conduct an authorised live test before marking a vendor/version supported.
