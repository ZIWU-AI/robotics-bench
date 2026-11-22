#!/usr/bin/env bash
# Resolve gateway auth for one (provider, agent, model) triple.
#
# The single place that knows how each gateway authenticates. Every trial and
# judge step used to carry its own copy of this case block -- eight of them
# across five workflows -- and they drifted: one mercor branch defaulted to
# ppapi's host, another kept an `echo "Using ppapi"` while exporting mercor
# creds. Copies of intricate logic are the bug; this file is the fix.
#
# Writes env for later steps to GATEWAY_ENV_FILE, and emits the agent flags each
# gateway needs as a base64'd JSON array via GATEWAY_OUTPUT_FILE. Callers decode
# element-wise (`jq -r '.[] | @base64'`, then base64 -d each): the Portkey --ae
# value embeds a newline, and jq strips \u0000 so a NUL delimiter is not an
# option -- a naive line or space split silently fuses the flags into one arg.
#
# Reads base URLs from the `gateways:` map in llm-config.yml so a host lives in
# exactly one place. A *_BASE_URL env override still wins, for a one-off.
set -euo pipefail

PROVIDER="${PROVIDER:-direct}"
AGENT="${AGENT:-}"
MODEL="${MODEL:-}"
CONFIG_FILE="${CONFIG_FILE:-.github/llm-config.yml}"
# GITHUB_ENV/GITHUB_OUTPUT in CI; overridable so the local harness can capture them.
ENV_FILE="${GATEWAY_ENV_FILE:-${GITHUB_ENV:?GITHUB_ENV or GATEWAY_ENV_FILE must be set}}"
OUT_FILE="${GATEWAY_OUTPUT_FILE:-${GITHUB_OUTPUT:?GITHUB_OUTPUT or GATEWAY_OUTPUT_FILE must be set}}"
TMPDIR_="${RUNNER_TEMP:-/tmp}"

FLAGS_JSON='[]'
add_flag() { FLAGS_JSON=$(jq -c --arg a "$1" --arg b "$2" '. + [$a, $b]' <<<"$FLAGS_JSON"); }
setenv()   { printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; }

# base_url for a gateway: explicit override env, else config, else built-in.
gateway_base_url() {
  local name="$1" override="$2" fallback="$3" v=""
  if [ -n "$override" ]; then
    v="$override"
  elif [ -f "$CONFIG_FILE" ]; then
    if command -v yq >/dev/null 2>&1; then
      v=$(yq -r ".gateways.${name}.base_url // \"\"" "$CONFIG_FILE" 2>/dev/null || echo "")
    else
      # Not every job installs yq (the trial matrices do not), and falling
      # through to the built-in default would silently bypass the config map --
      # exactly the drift this action exists to remove. python3 is always on the
      # runner; parse the one key we need without requiring PyYAML.
      v=$(CONFIG_FILE="$CONFIG_FILE" GW="$name" python3 - <<'PYEOF' 2>/dev/null || echo ""
import os, re
name, path = os.environ["GW"], os.environ["CONFIG_FILE"]
txt = open(path, encoding="utf-8").read()
block = re.search(r"^gateways:\n(.*?)(?=^\S|\Z)", txt, re.M | re.S)
if block:
    ent = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", block.group(1), re.M | re.S)
    if ent:
        u = re.search(r"^    base_url:\s*(\S+)", ent.group(1), re.M)
        if u:
            print(u.group(1).strip("\"'"))
PYEOF
)
    fi
  fi
  if [ -z "$v" ] || [ "$v" = "null" ]; then v="$fallback"; fi
  v="${v%/}"; printf '%s' "${v%/v1}"     # normalise to the bare host
}

# codex speaks only the Responses API and prefers a websocket transport that
# none of these gateways serve. Since 2026-08-14 a failed upgrade is fatal
# rather than falling back to HTTPS, so every trial died at 2-5s. The transport
# is gated per PROVIDER by supports_websockets, and the built-in `openai`
# provider hardcodes it true and cannot be overridden (merge_configured_model_
# providers uses entry().or_insert()), hence a distinct provider id selected via
# model_provider. Requires harbor >= 0.21.0, the first release where codex
# accepts a `config` kwarg; 0.20 dropped it silently.
write_codex_config() {
  local name="$1" base="$2" path="${TMPDIR_}/codex-${1}-config.toml"
  cat > "$path" <<EOF
model_provider = "${name}"

[model_providers.${name}]
name = "${name}"
base_url = "${base}/v1"
env_key = "OPENAI_API_KEY"
supports_websockets = false
EOF
  add_flag "--ak" "config=${path}"
}

case "$PROVIDER" in
  ppapi)
    HOST=$(gateway_base_url ppapi "${PPAPI_BASE_URL:-}" "https://app-us.ppapi.ai/v1")
    # One key, one host, two SDKs: the Anthropic SDK appends /v1/messages so it
    # takes the bare host; the OpenAI SDK wants host + /v1. Gemini rides the
    # OpenAI-compatible surface, so it needs no separate secret.
    setenv ANTHROPIC_API_KEY  "${PPAPI_API_KEY:-}"
    setenv OPENAI_API_KEY     "${PPAPI_API_KEY:-}"
    setenv ANTHROPIC_BASE_URL "$HOST"
    setenv OPENAI_BASE_URL    "$HOST/v1"
    if [ "$AGENT" = "codex" ]; then write_codex_config ppapi "$HOST"; fi
    echo "gateway: ppapi -> $HOST (agent=$AGENT model=$MODEL)"
    ;;
  mercor)
    HOST=$(gateway_base_url mercor "${MERCOR_BASE_URL:-}" "https://api.appintheloop.com/v1")
    # OpenAI-compatible chat-completions only; no Anthropic surface, so
    # claude-code cannot use this gateway. The key is scoped to a model group in
    # the router dashboard, so a slug resolves only if that group serves it.
    setenv OPENAI_API_KEY  "${MERCOR_API_KEY:-}"
    setenv OPENAI_BASE_URL "$HOST/v1"
    if [ "$AGENT" = "codex" ]; then write_codex_config mercor "$HOST"; fi
    echo "gateway: mercor -> $HOST (agent=$AGENT model=$MODEL)"
    ;;
  portkey)
    HOST=$(gateway_base_url portkey "${PORTKEY_BASE_URL:-}" "https://api.portkey.ai")
    if [ "$AGENT" = "claude-code" ]; then
      # claude-code strips the @provider/ slug, so the provider must ride in a
      # header. The real key goes in ANTHROPIC_CUSTOM_HEADERS via --ae (the only
      # env claude-code forwards); ANTHROPIC_AUTH_TOKEN just has to be non-empty.
      # Claude Code parses that var as NEWLINE-separated `Name: Value` pairs --
      # space-joining them polluted the key value and Portkey returned
      # "Invalid API Key".
      setenv ANTHROPIC_BASE_URL   "$HOST"
      setenv ANTHROPIC_AUTH_TOKEN "dummy"
      add_flag "--ae" "$(printf 'ANTHROPIC_CUSTOM_HEADERS=x-portkey-api-key: %s\nx-portkey-provider: %s' \
                          "${PORTKEY_API_KEY:-}" "${PORTKEY_PROVIDER:-@anthropic}")"
    elif [ "${MODEL#anthropic/}" != "$MODEL" ]; then
      # terminus-2 native Anthropic path -- prompt caching ON. Portkey wants
      # Bearer auth, so ANTHROPIC_API_KEY must stay unset.
      setenv ANTHROPIC_BASE_URL   "$HOST"
      setenv ANTHROPIC_AUTH_TOKEN "${PORTKEY_API_KEY:-}"
    else
      # OpenAI-compatible surface (openai/@provider/model), incl. Claude w/o caching.
      setenv OPENAI_API_KEY  "${PORTKEY_API_KEY:-}"
      setenv OPENAI_BASE_URL "$HOST/v1"
    fi
    echo "gateway: portkey -> $HOST (agent=$AGENT model=$MODEL)"
    ;;
  kimi)
    # Kimi Code official endpoint (KIMI_CODE_API_KEY), the same route the GPU
    # pool trials use (tools/isaac-model-comparison/routes.py): Anthropic
    # Messages at the bare host for claude-code, Responses at host + /v1 for codex.
    HOST=$(gateway_base_url kimi "" "https://api.kimi.com/coding")
    if [ "$AGENT" = "claude-code" ]; then
      setenv ANTHROPIC_API_KEY  "${KIMI_API_KEY:-}"
      setenv ANTHROPIC_BASE_URL "$HOST"
      # Every Claude Code model alias resolves to the Kimi model, including subagents.
      for name in ANTHROPIC_MODEL ANTHROPIC_DEFAULT_FABLE_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL \
                  ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL CLAUDE_CODE_SUBAGENT_MODEL; do
        add_flag "--ae" "${name}=${MODEL}"
      done
      add_flag "--ae" "CLAUDE_CODE_AUTO_COMPACT_WINDOW=262144"
      add_flag "--ae" "CLAUDE_CODE_MAX_CONTEXT_TOKENS=262144"
      add_flag "--ae" "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
      add_flag "--ae" "DISABLE_AUTOUPDATER=1"
    else
      setenv OPENAI_API_KEY  "${KIMI_API_KEY:-}"
      setenv OPENAI_BASE_URL "$HOST/v1"
      if [ "$AGENT" = "codex" ]; then
        path="${TMPDIR_}/codex-kimi-config.toml"
        cat > "$path" <<EOF
model_provider = "kimi_code_official"
model_context_window = 262144
model_auto_compact_token_limit = 220000

[model_providers.kimi_code_official]
name = "Kimi Code official"
base_url = "${HOST}/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
supports_websockets = false
EOF
        add_flag "--ak" "config=${path}"
      fi
    fi
    echo "gateway: kimi -> $HOST (agent=$AGENT model=$MODEL)"
    ;;
  ark|ark_paygo)
    # ark: Volcengine Ark Coding Plan (ARK_API_KEY). Anthropic Messages at the bare
    # host for claude-code, chat completions at host + /v3. Never the plain
    # /api/v3 host for this one: it bills outside the Coding Plan.
    # ark_paygo: the Ark model service. Anthropic Messages at /api/compatible,
    # chat completions at /api/v3; the model is a dated Model ID that must be
    # activated in the Ark console. Same key variable (an entry's `ark_key`).
    if [ "$PROVIDER" = "ark" ]; then
      HOST=$(gateway_base_url ark "" "https://ark.cn-beijing.volces.com/api/coding")
      OPENAI_HOST="$HOST/v3"
    else
      HOST=$(gateway_base_url ark_paygo "" "https://ark.cn-beijing.volces.com/api/compatible")
      OPENAI_HOST="https://ark.cn-beijing.volces.com/api/v3"
    fi
    if [ "$AGENT" = "claude-code" ]; then
      setenv ANTHROPIC_AUTH_TOKEN "${ARK_API_KEY:-}"
      setenv ANTHROPIC_BASE_URL   "$HOST"
      # Every Claude Code model alias resolves to the Ark model, including subagents.
      for name in ANTHROPIC_MODEL ANTHROPIC_DEFAULT_FABLE_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL \
                  ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL CLAUDE_CODE_SUBAGENT_MODEL; do
        add_flag "--ae" "${name}=${MODEL}"
      done
      add_flag "--ae" "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
      add_flag "--ae" "DISABLE_AUTOUPDATER=1"
    else
      setenv OPENAI_API_KEY  "${ARK_API_KEY:-}"
      setenv OPENAI_BASE_URL "$OPENAI_HOST"
    fi
    echo "gateway: $PROVIDER -> $HOST (agent=$AGENT model=$MODEL)"
    ;;
  none)
    # Agents that bring their own credential and endpoint (qoder: qodercli talks to
    # Qoder on QODER_PERSONAL_ACCESS_TOKEN, which run-trials.yml passes to qoder
    # trials only). Export nothing, so no LLM key reaches the step.
    echo "gateway: none (agent=$AGENT model=$MODEL)"
    ;;
  *)
    setenv ANTHROPIC_API_KEY "${DIRECT_ANTHROPIC_API_KEY:-}"
    setenv OPENAI_API_KEY    "${DIRECT_OPENAI_API_KEY:-}"
    echo "gateway: direct (agent=$AGENT model=$MODEL)"
    ;;
esac

printf 'extra_flags_b64=%s\n' "$(printf '%s' "$FLAGS_JSON" | base64 | tr -d '\n')" >> "$OUT_FILE"
