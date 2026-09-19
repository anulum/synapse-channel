# Paid provider worker configuration and price evidence

Quote validity is limited to seven days from observation.

Qwen keys are region-specific; select the matching official endpoint with
`--base-url` when the Singapore default does not match the account region.

`price_revision` is required. Optional `region` and `tier` identify the
applicable tariff. `cache_write_5m_per_million` and
`cache_write_1h_per_million` preserve separate cache-write tariffs where a
provider charges them. Leave dimensions that do not apply or have not been
verified as `null`; never infer a zero price from absence. The worker sends
text-only requests without explicit cache controls; its local estimate uses standard input
and output prices.

`synapse worker` defaults to local Ollama. A named paid provider requires all of `--allow-paid-api`, a customer-owned API key, `--paid-budget-usd`, and `--price-quote-file`. Missing values fail before any HTTP call. The key comes from the provider's default environment variable (`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `DASHSCOPE_API_KEY`, `MISTRAL_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, or `MOONSHOT_API_KEY`), or an explicit `--api-key-env`. Do not put keys in command arguments or quote files.

The quote file is a local JSON object with `provider`, `model`, `currency` (`USD`), `input_per_million`, `output_per_million`, `cache_read_per_million`, `cache_write_per_million`, `reasoning_per_million`, `source_url`, `source_date`, `observed_at` and `valid_until`. Price fields are numbers in USD per million tokens; unknown cache/reasoning dimensions are `null`. Timestamps are Unix seconds. Each file is an operator-reviewed snapshot for one exact model, region, tier and price revision. Recheck its official source and expiration before use. Provider pricing pages include [OpenAI](https://openai.com/api/pricing/), [DeepSeek](https://api-docs.deepseek.com/quick_start/pricing), [OpenRouter](https://openrouter.ai/models), [Qwen](https://help.aliyun.com/zh/model-studio/models), [Mistral](https://docs.mistral.ai/inference/pricing), [Anthropic](https://www.anthropic.com/pricing#api), [Google](https://ai.google.dev/gemini-api/docs/pricing), [xAI](https://docs.x.ai/developers/pricing), and [Moonshot](https://platform.moonshot.ai/docs/pricing). Provider-specific live prices are not asserted by this repository.

Example after creating and verifying a current quote file for `your-model`:

```sh
export OPENAI_API_KEY='...'
synapse worker --provider openai --model your-model \
  --allow-paid-api --paid-budget-usd 1.00 \
  --price-quote-file /path/to/current-quote.json
```

The guard reserves an estimate using request UTF-8 bytes plus protocol overhead and the worker's maximum output tokens. It refuses stale or mismatched quotes and further requests once its local estimated budget is exhausted. This is **not a hard billing cap**: provider tokenization, cache, reasoning, tool charges, errors, and concurrent processes can differ. Set an account-side spending limit for financial enforcement. CI and offline tests use only local HTTP fixtures and make no paid provider calls.
