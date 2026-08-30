# Providers and secrets

> **Status: beta (0.1.0b2).** Phases 1–9 are implemented and in daily use.
> Config formats and CLI commands are stable enough to build on; anything
> that has to change before 1.0 will be called out in
> [`CHANGELOG.md`](../CHANGELOG.md). See [`PLAN.md`](../PLAN.md) for what is
> still planned.

**Related:** [installation.md](installation.md) · [workspaces.md](workspaces.md) · [knowledge-bases.md](knowledge-bases.md) · [context-and-limits.md](context-and-limits.md)

A **provider profile** tells AIDA how to reach one LLM endpoint: a name, a
`kind`, a `base_url`, a `model`, and a `secret_ref` pointing at the OS
keychain (never a raw key). Profiles live in `~/.aida/providers.yaml`, and a
workspace picks one by name (see [workspaces.md](workspaces.md)).

## Provider profile fields

Each entry under `profiles:` in `providers.yaml` has:

| Field | Meaning |
|---|---|
| `kind` | `"openai_compat"` or `"anthropic"` (default `"openai_compat"`) |
| `base_url` | Endpoint URL, or `null`/omitted to use the SDK's default |
| `model` | Model name/id passed to the provider |
| `secret_ref` | Name of a keychain entry (see [Secrets](#secrets)), or `null` if the endpoint needs no key |
| `capability_notes` | Free-text note to yourself (e.g. "small local model — prefer lean MCP groups") |
| `max_tokens` | Cap on the reply length, in tokens. Omit/`null` to use the provider default — **note that Anthropic's default is 4096**, which a long generated report will hit (you'll see a "reply hit the max-tokens limit" notice when it does). **Do not set this to your model's total context size** — see the callout below the table |
| `temperature` | Sampling temperature. Omit/`null` for AIDA's default of `0.7` |
| `supports_vision` | `true` lets AIDA send images to this model — plot PNGs that MCP tools return, and image files you attach in the GUI. Defaults to `false`, because a model that can't accept images errors out the moment a tool returns one. Turn it on for any vision-capable model (Claude, GPT-4o-class, a local vision model) |
| `usd_per_m_input` / `usd_per_m_output` | Your actual price per million tokens, used for the session cost estimate. Set both to `0` for a free local model so the estimate stops pretending it costs anything; omit them and a generic default rate is used |
| `context_window` | The model's TOTAL context window, in tokens. Omit/`null` to fall back to `config.yaml`'s global `max_context_tokens` (see [context-and-limits.md](context-and-limits.md)). **Not the same field as `max_tokens` above** — `max_tokens` caps the *output* this profile generates; `context_window` is the model's total window that the output, the conversation history, and every enabled MCP server's tool schemas all have to fit inside together |

All six are optional and can be set per profile — that's the point of
having several profiles for the same endpoint (a cheap fast one and a
careful long-output one, say).

> **Common mistake: `max_tokens` set to the model's full context size.**
> `max_tokens` is *only* how many tokens this reply is allowed to generate
> — a good value is usually 4096-16000. It is easy to misread as "the
> model's total window" and set it to that instead (e.g. `max_tokens:
> 262000` on a 262k-context model). That single mistake breaks history
> budgeting unconditionally: `aida.core.context.history_budget` computes
> `context_window * 0.85 - max_tokens - tool_schema_tokens`, so once
> `max_tokens` alone is close to or larger than the window, the result is
> negative before any tool schema is even counted, and you'll see this in
> the logs on every turn:
>
> ```
> WARNING  aida.context: context budget clamped to the 8000-token floor:
> context_window=250000, reserved_output_tokens=262000, tool_schema_tokens=7815
> leaves only -57315 — consider a leaner MCP group or a larger context_window
> ```
>
> If you see that warning, check `max_tokens` first — a "leaner MCP group
> or larger context_window" rarely fixes it if `reserved_output_tokens`
> (which comes straight from `max_tokens`) is the real problem.
> `aida doctor`'s `max_tokens_vs_context_window` check catches this
> configuration and fails specifically on it.

```yaml
profiles:
  argo-claude-long:
    kind: anthropic
    base_url: "https://apps.inside.anl.gov/argoapi/"
    model: "claude-sonnet"
    secret_ref: "argo-claude"
    max_tokens: 16000           # long reports need the headroom
    temperature: 0.2            # analysis, not prose
    supports_vision: true       # let it actually look at the plots
    usd_per_m_input: 3.0
    usd_per_m_output: 15.0
    context_window: 200000      # Claude's real window — see context-and-limits.md
```

### Vision: letting the model see the plots

With `supports_vision: true`, an `ImageArtifact` a tool returns (a pyIrena
fit or residuals plot, for instance) is attached to the tool result the
model reads, so it can judge the fit rather than only reporting numbers.
Images you attach or drag into the GUI's input box are sent the same way.
Two limits worth knowing:

- Only the few most recent images are attached (a recency cap), and they're
  downscaled to bound token cost — that needs the `docs` extra (Pillow);
  without it the original-size bytes are sent.
- Tool-result images are only sent for `kind: anthropic`. OpenAI's
  chat-completions API rejects multi-part content on `tool` messages, so on
  `openai_compat` profiles only *your* attachments are sent as images.

`kind` only picks the wire protocol — OpenAI, Ollama, LM Studio, Unsloth
Desktop, or any other OpenAI-compatible server are all `kind: openai_compat`,
distinguished from each other by `base_url`/`model`, not by `kind`.

### `openai_compat` example — local Ollama model

```yaml
profiles:
  ollama-qwen:
    kind: openai_compat
    base_url: "http://localhost:11434/v1"
    model: "qwen2.5:14b"
    secret_ref: null            # local endpoints often need no key
    capability_notes: "small local model — prefer lean MCP groups"
```

### `anthropic` example — Claude via the ANL Argo proxy

```yaml
profiles:
  argo-claude:
    kind: anthropic
    base_url: "https://apps.inside.anl.gov/argoapi/"
    model: "claude-sonnet"
    secret_ref: "argo-claude"   # holds your ANL username, not an API key
    capability_notes: "cloud model via institutional proxy"
```

Claude direct (no proxy) is the same `kind: anthropic` shape with
`base_url: null` (SDK default) and `secret_ref` pointing at a keychain entry
holding your Anthropic API key instead of an ANL username.

## Embedding profiles

Embedding profiles have the exact same shape (`kind`, `base_url`, `model`,
`secret_ref`, `capability_notes`) but live under a separate
`embedding_profiles:` key in `providers.yaml`, because turning text into
vectors is a different capability than chat. They're only needed for RAG
knowledge bases — see [knowledge-bases.md](knowledge-bases.md). Today only
`kind: openai_compat` is implemented (there's no `anthropic` embeddings kind
— Anthropic has no first-party embeddings API):

```yaml
embedding_profiles:
  ollama-embed:
    kind: openai_compat
    base_url: "http://localhost:11434/v1"
    model: "nomic-embed-text"
    secret_ref: null
    capability_notes: "local embeddings — no data leaves the machine"
```

## Secrets

`providers.yaml` **never** stores a raw secret — only a `secret_ref` name.
The actual API key or ANL username is resolved at runtime, in this order:

1. An `AIDA_SECRET_<PROFILE>` environment variable, where `<PROFILE>` is the
   profile name upper-cased with `-` turned into `_` (e.g. `secret_ref:
   argo-claude` → `AIDA_SECRET_ARGO_CLAUDE`). Checked first, so it always
   wins — useful for headless/CI use where there's no OS keychain session.
2. The OS keychain (via the `keyring` package), under the service name
   `aida`.

To store a secret in the keychain, run:

```bash
aida config secret set argo-claude <your-ANL-username>
```

Then set that profile's `secret_ref: argo-claude` in `providers.yaml` (the
name, not the value).

To check whether a secret is set — this only reports `set`/`not set`, it
never prints the value itself:

```bash
aida config secret get argo-claude
```

To remove a stored secret:

```bash
aida config secret delete argo-claude
```

**Hard rule:** `providers.yaml` only ever stores a `secret_ref` name.
Pasting a real API key or username directly into `providers.yaml` defeats
the whole point of this design — don't do it.

## GUI: current limitation

The Qt Settings dialog currently shows a **read-only list** of configured
provider profiles (name, kind, model) — there is no way yet to create or
edit a provider or embedding profile from the GUI. Until that lands, editing
`~/.aida/providers.yaml` by hand (or scripting the edit) is the only way to
add, change, or remove a profile. `aida config secret set/get/delete`
(above) does work from the CLI today regardless.

## Full example

Rather than re-deriving every field, see
[`examples/config/providers.yaml`](../examples/config/providers.yaml) in the
repo for a fully commented example covering local (Ollama), direct Claude,
and Argo-proxied profiles for both `profiles:` and `embedding_profiles:`.

## Checking a profile actually works

`aida doctor` does a live reachability check against every configured
provider profile (not just "is it present in the file") — run it after
adding or editing a profile to confirm AIDA can actually reach it. For an
Anthropic profile this check is free: it calls the models-list endpoint
rather than sending a real (paid) completion, so running `doctor` — or
re-validating a profile in the Providers… dialog — doesn't cost anything.

```bash
aida doctor
```
