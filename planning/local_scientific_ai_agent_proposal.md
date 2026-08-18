# Proposal: Local Scientific AI Agent with MCP and GUI-Agnostic Architecture

## 1. Objective

Develop a lightweight, local-first AI agent application intended
primarily for scientific and technical work.

The application should provide a Claude Desktop--like conversational
interface while using locally served LLMs, particularly models served
through **Unsloth Desktop's OpenAI-compatible API**.

The primary motivation is that existing applications such as
AnythingLLM, Msty, and Witsy provide portions of the required
functionality but introduce substantial complexity while failing at
important details, particularly correct handling and display of rich MCP
tool results such as PNG images.

The application should therefore focus on a relatively small set of
capabilities and implement them reliably.

The central design principle is:

> **Separate the agent engine completely from the graphical user
> interface.**

The core should be a reusable Python package. A desktop GUI, web
interface, CLI, or other frontend should be replaceable without changing
the agent, MCP, workspace, or LLM logic.

------------------------------------------------------------------------

## 2. Primary Use Case

A user opens a project or experimental-data folder and interacts
conversationally with an AI agent.

For example:

> Open the USAXS data in this project, identify the measurements
> associated with sample X, plot the reduced SAXS data, compare them
> with yesterday's measurements, and save the resulting plot and a
> Markdown summary in the project folder.

The agent should be able to:

1.  Understand the request.
2.  Inspect the permitted project folder.
3.  Read appropriate files.
4.  Search existing documentation or RAG context.
5.  Invoke domain-specific MCP tools such as pyIrena.
6.  Receive text, images, structured data, and files from MCP tools.
7.  Display results correctly in the conversation.
8.  Continue reasoning based on tool results.
9.  Create new files in the project workspace.
10. Report clearly what it did and where outputs were saved.

This should occur within one continuous conversational agent session.

------------------------------------------------------------------------

## 3. Scope

The initial application does **not** need to reproduce everything
provided by AnythingLLM, Claude Desktop, Open WebUI, or VS Code.

The goal is specifically to implement the subset required for productive
local scientific work.

### Essential capabilities

-   Local OpenAI-compatible LLM endpoint
-   Streaming conversational chat
-   LLM tool calling
-   MCP client
-   Multiple MCP servers
-   stdio MCP transport
-   correct MCP text result handling
-   correct MCP image result handling
-   project/workspace folder access
-   file reading
-   file creation and modification
-   Markdown rendering
-   PNG/JPEG rendering
-   downloadable/openable generated files
-   conversation persistence
-   configurable tool permissions

### Important later capabilities

-   RAG over project documents
-   PDF support
-   DOCX support
-   XLSX support
-   images as input
-   web search
-   browser interaction
-   multiple projects/workspaces
-   configurable model providers
-   remote MCP servers
-   structured tables and interactive plots

### Explicitly not required initially

-   multi-user server
-   authentication system
-   cloud accounts
-   team collaboration
-   public web hosting
-   elaborate plugin marketplace
-   mobile application
-   dozens of LLM providers
-   complex workflow builder

Avoiding these features should keep the application substantially
simpler than general-purpose AI platforms.

------------------------------------------------------------------------

## 4. High-Level Architecture

The proposed architecture is:

``` text
                    ┌──────────────────────────┐
                    │        GUI / View        │
                    │                          │
                    │ PySide6 initially        │
                    │ NiceGUI optionally       │
                    │ CLI possible             │
                    └────────────┬─────────────┘
                                 │
                         Application API
                                 │
                    ┌────────────▼─────────────┐
                    │      Agent Core          │
                    │                          │
                    │ Conversation             │
                    │ Agent loop               │
                    │ Context management       │
                    │ Permissions              │
                    │ Event generation         │
                    └───────┬────────┬─────────┘
                            │        │
                 ┌──────────▼─┐   ┌──▼────────────┐
                 │ LLM Layer  │   │ MCP Manager   │
                 │            │   │               │
                 │ OpenAI API │   │ stdio         │
                 │ compatible │   │ HTTP later    │
                 └──────┬─────┘   └──────┬────────┘
                        │                │
                        ▼                ├── pyIrena
                 Unsloth Desktop        ├── macos-mcp
                                         ├── Firefox
                                         ├── Nexus
                                         └── future MCPs

                    ┌──────────────────────────┐
                    │       Workspace          │
                    │                          │
                    │ files                    │
                    │ search                   │
                    │ permissions              │
                    │ artifacts                │
                    └──────────────────────────┘

                    ┌──────────────────────────┐
                    │      Optional RAG        │
                    │                          │
                    │ extraction               │
                    │ chunking                 │
                    │ embeddings               │
                    │ retrieval                │
                    └──────────────────────────┘
```

The critical property is that **none of the core functionality depends
on a particular GUI framework**.

------------------------------------------------------------------------

## 5. Python as the Core Platform

Python should be used for the entire agent backend.

This provides straightforward access to:

-   OpenAI-compatible APIs
-   MCP Python SDK
-   scientific Python ecosystem
-   NumPy
-   SciPy
-   Pandas
-   Matplotlib
-   HDF5
-   NeXus
-   PDF/document libraries
-   embedding models
-   vector databases
-   existing scientific software

It also makes integration with existing scientific tools and pyIrena
natural.

The core should be usable independently:

``` python
agent = Agent(config)

session = agent.new_session(workspace="/path/to/project")

await session.send(
    "Plot all SAXS datasets associated with sample ABC."
)
```

No GUI object should be required for this code to operate.

------------------------------------------------------------------------

## 6. GUI Strategy

### Recommended long-term GUI: PySide6

PySide6/Qt is the preferred desktop frontend.

It provides a genuine cross-platform desktop application for macOS,
Windows, and Linux without requiring the application to operate as a
local web server.

A possible interface:

``` text
┌────────────────────┬─────────────────────────────────────┐
│ Workspace          │ Conversation                        │
│                    │                                     │
│ ▾ USAXS_data       │ User                                │
│   sample1.h5       │ Plot the SAXS data for sample 17.   │
│   sample2.h5       │                                     │
│   figures/         │ Agent                               │
│   notes.md         │ I found four measurements...        │
│                    │                                     │
│ MCP Servers        │ ┌───────────────────────────────┐   │
│ ✓ pyirena          │ │                               │   │
│ ✓ nexus            │ │       generated plot          │   │
│ ○ Firefox          │ │                               │   │
│                    │ └───────────────────────────────┘   │
│                    │                                     │
│ Model              │ Saved: figures/sample17.png         │
│ Muse-Glimmer-30B   │                                     │
└────────────────────┴─────────────────────────────────────┘
```

PySide6 should remain a thin presentation layer.

### NiceGUI as an optional prototype/frontend

NiceGUI remains useful for rapid development.

However, NiceGUI is fundamentally a locally served web application even
when displayed in a desktop-like native window.

It should therefore be considered an **optional frontend**, rather than
the foundation of the application.

If the backend architecture is properly separated, both could eventually
exist:

``` text
agent-core
   │
   ├── PySide6 desktop GUI
   ├── NiceGUI web GUI
   └── CLI
```

------------------------------------------------------------------------

## 7. Agent Core

The agent core should manage the interaction between user, LLM, tools,
MCP, workspace, context, and UI.

The basic loop is:

``` text
User message
     ↓
construct LLM context
     ↓
call Unsloth
     ↓
LLM response
     │
     ├── text ────────────────→ UI
     │
     └── tool call
             ↓
       execute tool
             ↓
       typed result
             ↓
       send result to UI
             ↓
       return result to LLM
             ↓
       continue agent loop
```

Multiple sequential tool calls must be supported.

A configurable maximum number of agent/tool iterations should prevent
runaway loops.

------------------------------------------------------------------------

## 8. LLM Provider Layer

The first provider should be a generic **OpenAI-compatible endpoint**.

Initial target:

``` text
Unsloth Desktop
      ↓
OpenAI-compatible API
      ↓
Muse-Glimmer / Gemma / other local model
```

The agent should not contain Unsloth-specific assumptions.

Define an abstraction such as:

``` python
class LLMProvider:
    async def complete(
        self,
        messages,
        tools,
        settings,
    ):
        ...
```

Then:

``` python
class OpenAICompatibleProvider(LLMProvider):
    ...
```

This permits later use with Unsloth, LM Studio, llama.cpp,
Ollama-compatible adapters, and remote OpenAI-compatible services
without modifying the agent.

------------------------------------------------------------------------

## 9. MCP Manager

MCP should be a first-class subsystem.

Use the official Python MCP SDK where practical.

The MCP manager should:

-   load MCP server definitions
-   launch stdio servers
-   maintain sessions
-   initialize servers
-   discover tools
-   expose tool schemas to the LLM
-   execute requested tools
-   preserve MCP result types
-   stop/restart servers
-   capture logs and errors

A configuration format close to the standard MCP ecosystem should be
retained:

``` json
{
  "mcpServers": {
    "pyirena": {
      "command": "/opt/miniconda3/envs/pyirena/bin/pyirena-mcp",
      "args": [],
      "env": {
        "PYIRENA_DATA_ROOT": "/Users/.../USAXS_data",
        "PYIRENA_MAX_ARRAY_POINTS": "500"
      }
    }
  }
}
```

This makes configurations portable from Claude Desktop, AnythingLLM, and
other MCP clients.

------------------------------------------------------------------------

## 10. Typed MCP Results

This is one of the most important architectural requirements.

**Never flatten all MCP output into text.**

For example, the MCP server may return:

``` json
{
  "type": "image",
  "data": "<base64 PNG>",
  "mimeType": "image/png"
}
```

This should immediately become an internal typed object:

``` python
@dataclass
class ImageArtifact:
    data: bytes
    mime_type: str
    filename: str | None = None
```

Similarly:

``` python
@dataclass
class TextArtifact:
    text: str

@dataclass
class FileArtifact:
    path: Path
    mime_type: str | None = None

@dataclass
class JsonArtifact:
    value: dict | list
```

The pipeline becomes:

``` text
MCP ImageContent
       ↓
base64 decode
       ↓
ImageArtifact
       ↓
┌──────────────┬────────────────┐
│              │                │
UI renderer    LLM context      artifact store
```

The GUI therefore never has to guess whether a long string is actually
an image.

This directly addresses the behavior observed with AnythingLLM, Msty,
and Witsy.

------------------------------------------------------------------------

## 11. Application Event Model

Communication between the core and GUI should be event based.

Possible events:

``` python
TextStarted()
TextDelta(text)
TextFinished()

ToolCallStarted(server, tool, arguments)
ToolCallFinished(server, tool)

ImageArtifactCreated(image)
FileArtifactCreated(file)
TableArtifactCreated(table)

AgentError(error)
MessageFinished()
```

The GUI subscribes to these events. This makes the core independent of
Qt, NiceGUI, or any other presentation system and makes testing
considerably easier.

------------------------------------------------------------------------

## 12. Workspace

A conversation should optionally be associated with a **workspace
root**.

The agent may access files within that directory but should not
automatically have unrestricted filesystem access.

Native workspace tools could include:

``` text
list_directory
find_files
search_text
read_text_file
write_text_file
copy_file
move_file
create_directory
get_file_metadata
```

Initially, these do not need to be MCP tools.

Native implementation is simpler and allows the application to enforce
whether a requested path is inside the permitted workspace.

Domain-specific operations should remain MCP tools.

------------------------------------------------------------------------

## 13. File and Document Support

The workspace should eventually recognize common scientific and office
files.

### Phase 1

-   TXT
-   Markdown
-   JSON
-   CSV
-   PNG
-   JPEG
-   HDF5/NeXus where appropriate

### Phase 2

-   PDF
-   DOCX
-   XLSX
-   PPTX

Reading should preferably produce structured internal content rather
than blindly converting everything into plain text.

The agent should also be able to generate new documents. Generated files
become `FileArtifact` objects and are displayed in the conversation with
actions such as Open and Show in Finder.

------------------------------------------------------------------------

## 14. RAG

RAG should **not be part of the first implementation**.

Initially, allow the agent to list files, search filenames/text, select
relevant documents, and read relevant content.

For modest project folders this may be sufficient.

RAG should be added when document collections become too large for
direct search/read operations.

A later RAG pipeline could be:

``` text
Documents
    ↓
format-specific extraction
    ↓
chunking
    ↓
local embedding model
    ↓
vector index
    ↓
retrieval
    ↓
LLM context
```

Keep this subsystem optional and independent.

------------------------------------------------------------------------

## 15. Web Search

Web search should also be modular.

Possible implementations include:

-   dedicated MCP search server
-   browser MCP
-   direct search API
-   Firefox automation

The agent core should simply see another tool such as
`web_search(query)` and should not care which underlying implementation
provides it.

------------------------------------------------------------------------

## 16. Permissions and Safety

Tool permissions should be explicit and understandable.

Potentially destructive actions should require user confirmation,
including:

-   deleting files
-   overwriting existing data
-   shell commands
-   moving files outside workspace
-   modifying external applications
-   sending information over the Internet

Local inference does not automatically imply that every tool operation
is local, so network-capable tools should be identifiable.

------------------------------------------------------------------------

## 17. Conversation Persistence

Use SQLite initially.

Store conversations, messages, tool calls, tool results, artifact
metadata, workspace information, model configuration, and timestamps.

Large binary artifacts such as PNG files should normally remain files
rather than being stored as base64 inside the database.

Project-generated outputs intended as actual work products should
instead be saved in the workspace.

------------------------------------------------------------------------

## 18. Configuration

Configuration should remain human-readable and portable.

Potential structure:

``` text
~/.local-scientific-agent/
    config.yaml
    mcp.json
    agent.db
    artifacts/
    logs/
```

Model configuration, MCP definitions, permissions, and UI preferences
should be separate where practical.

The MCP configuration should remain close to standard MCP JSON rather
than inventing an application-specific format.

------------------------------------------------------------------------

## 19. Logging and Diagnostics

The application should make it clear whether a failure occurred in the
LLM, MCP client, MCP server, tool, or UI.

For every tool call, record the server, tool, arguments, timing, result
content types, MIME types, payload sizes, and status.

A developer/debug panel should be able to display raw MCP responses.

------------------------------------------------------------------------

## 20. Suggested Repository Structure

``` text
scientific-agent/
│
├── src/
│   └── scientific_agent/
│
│       ├── core/
│       │   ├── agent.py
│       │   ├── conversation.py
│       │   ├── events.py
│       │   └── context.py
│       │
│       ├── llm/
│       │   ├── base.py
│       │   └── openai_compatible.py
│       │
│       ├── mcp/
│       │   ├── manager.py
│       │   ├── server.py
│       │   ├── config.py
│       │   └── results.py
│       │
│       ├── workspace/
│       │   ├── workspace.py
│       │   ├── files.py
│       │   ├── search.py
│       │   └── permissions.py
│       │
│       ├── artifacts/
│       │   ├── base.py
│       │   ├── image.py
│       │   ├── file.py
│       │   └── table.py
│       │
│       ├── rag/
│       │   ├── index.py
│       │   ├── extraction.py
│       │   └── retrieval.py
│       │
│       ├── persistence/
│       │   └── database.py
│       │
│       └── ui/
│           ├── pyside/
│           └── nicegui/
│
├── tests/
├── examples/
├── pyproject.toml
└── README.md
```

The `ui` package must depend on `core`, but **core must never depend on
`ui`**.

------------------------------------------------------------------------

## 21. Development Phases

### Phase 1 --- Prove the core agent loop

Implement only:

-   OpenAI-compatible Unsloth connection
-   conversation
-   tool schema transmission
-   MCP stdio connection
-   MCP tool discovery
-   MCP tool execution
-   text results
-   image results

Use pyIrena as the reference MCP server.

**Success criterion:** Muse-Glimmer receives a request, calls pyIrena
through MCP, pyIrena returns a PNG as MCP `ImageContent`, and Python
correctly receives and decodes the image.

No GUI is necessary to prove this.

### Phase 2 --- Minimal desktop GUI

Create a basic PySide6 interface with conversation history, chat input,
send/stop buttons, streaming model output, tool-call indicators, and
inline PNG display.

**Success criterion:** Reproduce the working Claude Desktop pyIrena
interaction using Unsloth and the new application.

### Phase 3 --- Workspace

Add:

-   choose project folder
-   file tree
-   read files
-   write files
-   workspace restrictions
-   generated artifacts
-   open/reveal generated files

**Success criterion:** Ask the agent to inspect data, perform MCP
analysis, show the plot, and save a Markdown report and PNG into the
project.

### Phase 4 --- MCP management

Add a configuration UI for:

-   add/remove server
-   start/stop
-   enable/disable
-   tool list
-   individual tool permissions
-   logs
-   raw tool-result inspection

Import existing standard `mcp.json` configurations where possible.

### Phase 5 --- Document support

Add PDF, DOCX, XLSX, image input, and richer file previews.

Keep document parsing independent of the GUI.

### Phase 6 --- RAG

Only after direct workspace access becomes insufficient, add document
indexing, local embeddings, retrieval, index status, and incremental
updates.

RAG should augment direct file access, not replace it.

### Phase 7 --- Additional agent capabilities

Potential additions:

-   web search
-   Firefox MCP
-   macOS MCP
-   Nexus MCP
-   Python execution
-   interactive plots
-   multiple agents/models
-   remote models
-   MCP Apps or other rich interactive tool outputs

------------------------------------------------------------------------

## 22. Key Design Decisions

1.  **GUI-independent core** --- the application is an agent engine with
    a GUI attached, not a GUI containing agent logic.
2.  **Typed results throughout** --- images remain images, files remain
    files, and structured data remains structured.
3.  **MCP for external/domain capabilities** --- MCP is the primary
    extension mechanism.
4.  **Native workspace operations** --- basic project filesystem
    operations do not need to become MCP calls.
5.  **OpenAI-compatible model interface** --- do not couple the
    application to Unsloth.
6.  **Local-first** --- no cloud infrastructure should be required.
7.  **RAG comes later** --- do not introduce embeddings/vector databases
    until direct file operations demonstrate that they are insufficient.
8.  **Diagnostics are a feature** --- every model/tool/MCP transition
    should be inspectable.

------------------------------------------------------------------------

## 23. Initial Technology Choices

  Component              Choice
  ---------------------- ------------------------------------------------
  Language               Python
  Desktop GUI            PySide6 / Qt 6
  LLM protocol           OpenAI-compatible API
  Initial model server   Unsloth Desktop
  Initial model          Muse-Glimmer-30B
  MCP                    Official Python MCP SDK
  MCP transport          stdio initially
  Persistence            SQLite
  Configuration          YAML + standard-style `mcp.json`
  Workspace              `pathlib` + native Python
  Images                 native bytes/Pillow/Qt
  Scientific data        NumPy/Pandas/h5py as needed
  RAG                    deferred
  Packaging              Python package initially; desktop bundle later

------------------------------------------------------------------------

## 24. First Concrete Prototype

The first prototype should deliberately be extremely small.

It should do only this:

``` text
Start application
       ↓
Connect to Unsloth
       ↓
Start pyIrena MCP
       ↓
Discover pyIrena tools
       ↓
User enters:
"Plot ..."
       ↓
Muse-Glimmer requests MCP tool
       ↓
Python executes tool
       ↓
MCP returns PNG ImageContent
       ↓
Python decodes PNG
       ↓
GUI displays PNG inline
       ↓
tool result returned to Muse-Glimmer
       ↓
Muse-Glimmer finishes response
```

Do **not** initially implement RAG, Office files, Firefox, Nexus, web
search, multiple projects, sophisticated settings, or elaborate UI
styling.

This prototype tests the hardest and most important part of the
architecture.

If it works, most subsequent capabilities are incremental additions.

------------------------------------------------------------------------

## 25. Longer-Term Goal

The eventual application should feel less like an AI platform and more
like a **local scientific workbench with an agent interface**.

The desired interaction is:

> Here is the project directory. You may inspect these files and use
> these specific tools. Help me perform the work, show me what you
> produce, and save useful outputs back into the project.

The model itself remains replaceable.

The MCP servers remain independently developed and reusable.

The GUI remains replaceable.

The project workspace remains ordinary files and directories rather than
being trapped inside an application-specific database.

That separation should produce a system substantially simpler, more
transparent, and easier to maintain than attempting to reproduce a
general-purpose application such as AnythingLLM.

The immediate development target should therefore be **Phase 1 + Phase
2: a minimal Python/PySide6 agent capable of using an Unsloth-hosted
model to call pyIrena through MCP and correctly display returned PNG
images inline.**
