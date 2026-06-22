# mindnode-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

**English** | [日本語](README.ja.md)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant
(Claude, etc.) **read and write your [MindNode](https://mindnode.com) mind maps
directly** — by parsing the `.mindnode` file format itself. No AppleScript, no
Shortcuts, no export/import dance.

Ask your assistant to *"read my project map"*, *"add these three ideas under
Marketing"*, *"connect the API node to the Auth node"*, or *"turn this outline
into a new mind map"* — and it operates on the real files MindNode syncs.

> **Why this exists:** MindNode dropped its AppleScript dictionary, and its
> Shortcuts/URL-scheme automation is thin. But a `.mindnode` document is just a
> package whose `contents.xml` is an Apple **binary plist** — a clean recursive
> node tree. Read/write that, and you get full programmatic control.

## Requirements

- **macOS** with [MindNode](https://mindnode.com) installed (format version 9 —
  current MindNode releases)
- **Python 3.11+** and [uv](https://github.com/astral-sh/uv)
- An MCP client (e.g. [Claude Code](https://claude.com/claude-code))

## Install

```bash
git clone https://github.com/masamitsu-konya/mindnode-mcp.git
cd mindnode-mcp
uv sync
```

Register it with Claude Code (user scope = available everywhere):

```bash
claude mcp add --scope user mindnode -- uv --directory "$PWD" run mindnode-mcp
```

Then start a new session and run `/mcp` (or `claude mcp list`) to confirm it
shows **mindnode ✔ Connected**.

By default it finds your MindNode documents in the iCloud container
automatically. Point it elsewhere with the `MINDNODE_DOCS_DIR` environment
variable (a local library, or a fixture folder for testing).

## Usage

Talk to your assistant in plain language — it picks the right tool. Examples:

| You say | What happens |
|---------|--------------|
| "List my mind maps" | `list_documents` → names + dates, newest first |
| "Read my *Project Plan* map" | `read_document` → the full node tree as JSON |
| "Search all my maps for 'pricing'" | `search_nodes` → every matching node + its document |
| "Add 'Hire a designer' under the Team node in *Roadmap*" | `add_node` |
| "Connect 'Frontend' to 'API' with the label 'calls'" | `add_connection` |
| "Tag the 'Launch' node as #urgent" | `add_tag` (creates the tag if new) |
| "Mark 'Ship v1' as done" | `set_task` |
| "Attach ~/Desktop/wireframe.png to the Design node" | `attach_image` |
| "Make a new map 'Q3 Goals' with branches Sales, Product, Hiring" | `create_map` |

Nodes can be referenced by their text (a case-insensitive substring is enough)
or by their exact id (ids come back from `read_document`).

## Tools

| Tool | Kind | What it does |
|------|------|--------------|
| `list_documents` | read | All `.mindnode` files, newest first |
| `read_document` | read | Mind maps as `{id, text, note?, task?, tags?, attachment?, children?}` trees, plus `connections` and the document's tag list |
| `search_nodes` | read | Substring search over node text + notes, in one document or all |
| `add_node` | write | Add a node under a parent (by id or text), with optional note |
| `add_connection` | write | Cross-link two existing nodes with an optional label and arrow direction |
| `add_tag` / `remove_tag` | write | Tag / untag a node (tags are document-wide and auto-created) |
| `set_task` | write | Turn a node into a checkbox task and set done / todo |
| `attach_image` | write | Attach a local image to a node (copied into the package's `resources/`) |
| `create_map` | write | Create a new `.mindnode` from a title + (optionally nested) outline |

### Connections (cross-links)

Free links between any two nodes, independent of the parent/child tree (stored
at `canvas.crossConnections[]`). `add_connection(document, start, end, label?,
direction?)` — `direction` ∈ `forward` (default) / `backward` / `both` / `none`.
`read_document` returns each as `{id, start_id, end_id, start_text, end_text,
direction, label?}`.

### Tags, tasks, attachments

`read_document` surfaces these per node (and lists all tag names at the top):

- **Tags** — normalized: `canvas.tags[]` defines `{tagID, name, color}`,
  `node.tags[]` references tagIDs. `add_tag` auto-defines a tag of that name if
  it doesn't exist, then attaches it (idempotent).
- **Tasks** — `node.task = {state, uuids}`, where `state` 1 = todo, 2 = done.
  `set_task(document, node, done)` toggles it.
- **Attachments (images)** — `node.attachment = {fileName, size, tintKind,
  type}`; image bytes live in `resources/<fileName>`. `attach_image` copies the
  file in and links it, clamping display width to 300px (matching MindNode).

## How it works

A `.mindnode` document is a **package directory**. Its `contents.xml` — despite
the extension — is an Apple **binary plist** holding the mind map (format
**version 9**):

```
canvas.mindMaps[].mainNode        # root node of each map
  ├─ nodeID                       # UUID
  ├─ title.text                   # the node's text, stored as small HTML
  ├─ note / task / tags / attachment
  └─ subnodes[]                   # children (same shape, recursive)
canvas.crossConnections[]         # free links between nodes
canvas.tags[]                     # tag definitions
```

The server reads and writes this with Python's standard `plistlib`, so it needs
**no third-party plist libraries**. Node text round-trips through a minimal
HTML encode/decode (with proper escaping).

## Write safety

These tools mutate your real files, so every write:

- **backs up** `contents.xml` to a timestamped `.bak-*` first,
- writes to a temp file then **atomically replaces** it (no partial writes),
- **preserves keys it didn't author** (styling, layout, print info), and
- drops the stale QuickLook preview so it regenerates.

`create_map` clones an existing document as a structural skeleton (keeping all
opaque auxiliary files valid) and overwrites only the node tree.

> **Caveat — document open in MindNode.** Writes go straight to disk. If MindNode
> (or another device via iCloud) has the same document open, its next autosave
> can clobber the change, or you may get an iCloud conflict copy. Close the
> document in MindNode before writing, or reopen it afterwards to pick up the
> edit. Backups make this recoverable, but it's cleaner to avoid.

## Development

```bash
uv run python tests/smoke.py
```

The smoke test exercises reads against your real documents (read-only) and all
writes against throwaway temp copies — it never modifies your actual maps. It
also asserts that generated structures (nodes, connections, tags, tasks, image
attachments) match the schema of real MindNode files key-for-key.

## Status & roadmap

- [x] Read — list / read / search
- [x] Write — add_node / create_map
- [x] Connections / cross-links — read + add_connection
- [x] Tags, tasks, image attachments — read + write
- [ ] Non-image attachments (links, stickers)
- [ ] Tag color palette / rename, task removal
- [ ] Connection waypoint editing

## License

[MIT](LICENSE) © 2026 Masamitsu Konya
