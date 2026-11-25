# Model settings and evaluation

Qwen is the default. The administrator setting is stored in PostgreSQL and applies to new requests. Confirm there are no in-flight customer requests before temporarily changing the global provider in the isolated demo. An idle Streamlit WebSocket can stay connected.

## Enable Gemini for the synthetic demo

1. In the private `.env.compose`, configure `GEMINI_API_KEY` and set `GEMINI_MODEL=gemini-3.5-flash-lite` if available to the Google project. Keep the key out of Git.
2. With the [demo project's](DEMO.md) `dc` command, apply the environment with `dc up -d --no-deps backend`. Confirm Backend readiness.
3. Open **Administration → Model settings** as an administrator. Save Gemini. FastAPI performs a real structured-tool probe before persisting the choice.
4. Open a fresh customer chat, opt in to cloud processing and ask for P022's price. The fixture records RM220,000. The actual Backend response includes the model ID and Gemini HTTP request count.
5. Restore Qwen in all outcomes. Check `curl -fsS http://127.0.0.1:38000/agent/provider` and start a fresh local chat.

Gemini receives the consented question and recent history. The backend rejects common contact patterns. Use synthetic prompts only. Lead contact data stays in the separate enquiry flow. Qwen failures do not trigger cloud fallback. The rate limiter applies to synchronous Gemini SDK requests at 10 per rolling minute by default, configurable downwards.

## Run a cloud evaluation

Follow the fresh demo, seed the unmodified fixtures, and verify provider quota and budget in Google AI Studio. Keep Qwen selected globally and use an authorized administrator token supplied privately as `EVALUATION_ADMIN_TOKEN`. The evaluation runner uses a per-request override.

```bash
python -m evaluation.run --provider cloud --confirm-synthetic-sandbox --confirm-quota-budget \
  --smoke --output evaluation/results/gemini-smoke.json
python -m evaluation.run --provider cloud --confirm-synthetic-sandbox --confirm-quota-budget \
  --smoke-report evaluation/results/gemini-smoke.json --output evaluation/results/gemini-01.json
```

If a run stops, preserve its output. Pass it to `--resume` along with the original passing smoke report and a new output path. The resume guard checks the dataset, provider and Backend lineage. The original raw evaluation artifacts are kept outside the portfolio snapshot. Public metrics summarize the observed results.

## Verified acceptance

A real Streamlit AppTest exercised Qwen → Gemini → Qwen. PostgreSQL retained both settings when read independently. A new synthetic P022 chat answered RM220,000 through Gemini, with two observed SDK HTTP requests. Another fresh chat answered through Qwen after restoration. The first provider save returned HTTP 409 at its capability gate. A standalone synthetic probe and a single UI retry passed. Manual browser switching was not performed. [Measured comparison](../evaluation/MODEL_COMPARISON.md)
