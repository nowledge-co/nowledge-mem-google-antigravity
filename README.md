# Nowledge Mem -- Google Antigravity Plugin

> Bring your Nowledge Mem knowledge base into Google Antigravity with persistent context, memory rules, and agent skills.

This package is the **Google Antigravity-native product surface** for Nowledge Mem.

It is deliberately **hybrid**:

- Google Antigravity loads memory rules plus lifecycle hooks for Context Bundle / Working Memory startup context and session capture
- the plugin exposes local Nowledge Mem MCP tools for lower-friction retrieval and memory writes
- bundled skills teach Antigravity when to recall, distill, save threads, and create handoff summaries
- Antigravity can still call `nmem` directly whenever it needs a more flexible path

The recommended setup is simple and stable: Google Antigravity on top, MCP for direct retrieval tools, and `nmem` for hooks, thread save, remote auth, and command fallback.

## Requirements

- [Google Antigravity 2.0](https://antigravity.google)
- [Nowledge Mem](https://mem.nowledge.co) running locally, or a reachable remote Nowledge Mem server
- `nmem` CLI in your `PATH` (or installed via standard Linux packages at `/usr/lib/nowledge-mem/nmem` / `/usr/lib64/nowledge-mem/nmem`)

If Nowledge Mem is already running on the same machine through the desktop app, install the bundled CLI from **Settings -> Preferences -> Developer Tools -> Install CLI**. That gives Antigravity direct access to the local Mem instance. The plugin automatically resolves system installation paths if sandboxed subshells restrict user `$PATH` symlinks.

Verify connection:

```bash
nmem status
```

For the default same-machine setup, `nmem status` should show `http://127.0.0.1:14242 (default)`.

## Install

For local development or a repository checkout install, you can link the plugin folder to one of the locations scanned by Antigravity:

### Option 1: Workspace Level (Active workspace only)

Place or symlink the plugin folder inside `.agents/plugins/` (or `_agents/plugins/`) at the root of your opened workspace:

```bash
mkdir -p .agents/plugins
ln -s /path/to/nowledge-mem-google-antigravity .agents/plugins/
```

### Option 2: Global Level (All workspaces)

Place or symlink the plugin folder inside `~/.gemini/config/plugins/` in your user home directory:

```bash
mkdir -p ~/.gemini/config/plugins
ln -s /path/to/nowledge-mem-google-antigravity ~/.gemini/config/plugins/
```

Alternatively, you can clone the repository directly into your global plugins directory:

```bash
git clone https://github.com/nowledge-co/nowledge-mem-google-antigravity.git ~/.gemini/config/plugins/nowledge-mem
```

Restart Google Antigravity after linking or cloning.

Release packaging notes live in [`RELEASING.md`](./RELEASING.md).

## What You Get

**Automatic lifecycle hooks**

- **PreInvocation Hook**: Automatically loads Context Bundle when available, with Working Memory as the lightweight fallback, and injects it as situational context at the start of the session. Prioritizes direct native HTTP REST transport (<30ms latency), falling back to CLI subprocess execution. Concurrently launches a non-blocking background thread to connect and sync active host skills (`nmem skills connect antigravity` and `nmem skills sync`).
- **PostInvocation Hook**: Evaluates mid-session runtime state (such as pending offline sessions in `~/.nowledge-mem/antigravity_unsynced.json`), triggers asynchronous background synchronization, and throttles informational notifications to at most once per conversation session.
- **Stop Hook**: Automatically imports conversation messages from Antigravity's `transcript.jsonl` log into Nowledge Mem under the current conversation ID when execution completes. Defers capture when background tasks are still running (`fullyIdle: false`), and prioritizes incremental tail reconciliation (`/threads/{id}/reconcile-tail`) to eliminate duplicate messages and reduce payload size. Prioritizes direct native HTTP REST transport, falling back to CLI execution and local offline buffer queuing (`~/.nowledge-mem/antigravity_unsynced.json`).
- **Space Resolution & Verification**: Resolves the active space by prioritizing explicit environment overrides (`NMEM_SPACE`/`NMEM_SPACE_ID`) and project config (`.nmemspace`/`.nowledge/config.json`). Dynamically detected candidate spaces from workspace directory names are verified against existing backend spaces; if the space does not exist on the server, Antigravity safely falls back to `default`.

**Bundled MCP**

- Local same-machine installs expose `nowledge-mem` MCP tools at `http://127.0.0.1:14242/mcp/` automatically.
- The startup hook automatically synchronizes `mcp_config.json` with effective client settings (`~/.nowledge-mem/config.json` or `NMEM_API_URL`/`NMEM_API_KEY`). For remote Mem, `mcp_config.json` is updated dynamically with the remote server URL and authentication headers (`Authorization: Bearer` and `X-NMEM-API-Key`).
- The committed `mcp_config.json` is a local placeholder template. Do not commit a real `nmem_...` API key.

**Persistent context rules**

- `rules/nowledge-mem.md` tells Antigravity how to route recall across Context Bundle, Working Memory, distilled memories, conversation threads, handoff summaries, and positional CLI fallback signatures.

**Agent skills**

- `nmem-fs-explore`
- `nmem-memory-distill`
- `nmem-memory-search`
- `nmem-memory-working`
- `nmem-skill-load`
- `nmem-skill-manage`
- `nmem-skill-propose`
- `nmem-status`
- `nmem-thread-handoff`
- `nmem-thread-save`

## Local vs Remote & Configuration Precedence

By default, both `nmem` and the bundled MCP server point to the local Mem server at `http://127.0.0.1:14242`.

Nowledge Mem resolves connection and space settings in the following strict order of precedence:

1. **Environment Variables**: `NMEM_API_URL`, `NMEM_API_KEY`, `NMEM_SPACE` (or `NMEM_SPACE_ID`)
2. **Local Plugin / Workspace Config (`.config.json`)**: Place a `.config.json` file at the root of the plugin (or workspace root). This file is git-ignored and allows configuring server endpoints, credentials, default space, and future settings locally:
   ```json
   {
     "apiUrl": "https://mem.example.com",
     "apiKey": "nmem_your_key",
     "space": "my-project-space"
   }
   ```
3. **Global Client Config**: `~/.nowledge-mem/config.json` (managed via `nmem config client set ...`)
4. **Local Defaults**: `http://127.0.0.1:14242` and `default` space.

For global remote Mem setup via CLI, run:

```bash
nmem config client set url https://mem.example.com
nmem config client set api-key nmem_your_key
```

If you need a temporary override for one session, launch Antigravity from a shell where `NMEM_API_URL`, `NMEM_API_KEY`, or `NMEM_SPACE` are exported.

For MCP tools in remote mode, generate the host config:

```bash
nmem config mcp show --host google-antigravity
```

Paste the generated JSON into Antigravity's custom MCP config (`~/.gemini/config/mcp_config.json` or modified raw via the MCP Store).


## Direct `nmem` Use Is Always Allowed

The bundled skills are convenience paths, not a cage. Antigravity should freely compose direct `nmem` commands when that is clearer or more flexible.

Examples:

```bash
nmem --json wm read
nmem --json m search "auth token rotation" --mode deep --importance 0.7
nmem --json m search "auth token rotation" --mode deep --importance 0.7 --space "Research Agent"
nmem --json m add "JWT refresh failures came from clock skew between the gateway and API nodes." -t "JWT refresh failures traced to clock skew" -i 0.9 --unit-type learning -l auth -l backend -s google-antigravity
nmem status
```

## Thread Save vs Handoff

Antigravity supports two separate save paths:

- **Thread Save** (`nmem-thread-save` skill / `/nmem-thread-save`): Imports the **real session messages** into Nowledge Mem. The Stop hook performs this import automatically at the end of the session, but the skill is available for manual mid-session triggers.
- **Handoff Save** (`nmem-thread-handoff` skill / `/nmem-thread-handoff`): Creates a **compact resumable handoff summary** with Goal, Decisions, Files, Risks, and Next. Use this when you want a lightweight restart point rather than the full transcript.

Use `nmem-memory-distill` for durable atomic knowledge, `nmem-thread-save` for the full session, and `nmem-thread-handoff` for a resumable handoff.

## Workspace Skill Management

- **Install/Update Skill** (`nmem-skill-manage` skill / `/nmem-skill-manage`): Evaluates the project context, lists active/archived/candidate skills from Nowledge Mem, and helps recommend and install them into `<workspace-root>/.agents/skills/<skill-folder>/`. Users can choose to commit these skills to git or keep them local-only (via git-exclude).
- **On-Demand Skill Loader** (`nmem-skill-load` skill / `/nmem-skill-load <query>`): Dynamically searches candidate/compiled skills on Nowledge Mem and loads them into the active turn (Ephemeral Mode) or installs them locally (Persistent Mode). Triggered explicitly via `/nmem-skill-load <query>` or automatically when Antigravity detects an unhandled domain task with an available skill definition.

## Development & Testing

Run unit tests for lifecycle hooks:

```bash
make test
# or: npm run test
```

Run container integration test suite using `docker.io/nowledgelabs/mem:latest` published on a random host port:

```bash
make test-integration
# or: python3 -m pytest -v tests/integration/
```

Run full plugin validation:

```bash
make validate
# or: npm run validate
```


## Links

- [Architecture Deep Dive](./ARCHITECTURE.md)
- [Documentation](https://mem.nowledge.co/docs/integrations/google-antigravity)
- [Nowledge Mem](https://mem.nowledge.co)
- [Discord](https://nowled.ge/discord)
- [GitHub](https://github.com/nowledge-co/nowledge-mem-google-antigravity)
