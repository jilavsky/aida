# Providers and secrets

> **Status: pre-alpha.** Config formats and CLI commands may change without
> notice until Phase 5. See [`PLAN.md`](../PLAN.md) for the full roadmap.

**Related:** [installation.md](installation.md) · [workspaces.md](workspaces.md) · [knowledge-bases.md](knowledge-bases.md)

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
adding or editing a profile to confirm AIDA can actually reach it:

```bash
aida doctor
```
