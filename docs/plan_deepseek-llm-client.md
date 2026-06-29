# DeepSeek LLM Client Implementation

## Goals
- Add the first real LLM provider using DeepSeek's OpenAI-format chat API.
- Use `deepseek-v4-flash` by default with `DEEPSEEK_API_KEY` from the environment only.
- Summarize the existing compact `SummaryInput` payload with source-aware prompts.
- Add a CLI command that can summarize an existing payload file or build one from SQLite.
- Run automated tests and a live DeepSeek smoke test that writes a temporary response file.

## Checklist
- Extend LLM config with DeepSeek base URL, timeout, temperature, max tokens, and thinking toggle.
- Update the LLM protocol to accept a summary payload instead of raw collected items.
- Implement prompt construction for X and Reddit market-intelligence summaries.
- Implement a DeepSeek HTTP client with clear auth, validation, rate-limit, credit, and server error handling.
- Register/build LLM clients from config.
- Add `stock-sum llm summarize --profile default --payload PATH --output PATH`.
- Add unit tests for config, prompt shape, client request/error handling, and CLI smoke.
- Verify with pytest, config validation, `git diff --check`, and a live temp-file summary if `DEEPSEEK_API_KEY` is available.
