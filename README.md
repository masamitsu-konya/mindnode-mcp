# mindnode-mcp

An MCP server that lets Claude read and write **MindNode** mind maps directly,
by parsing their on-disk format — no AppleScript or Shortcuts required.

## How it works

A `.mindnode` document is a package directory whose `contents.xml` is an Apple
**binary plist** holding a recursive node tree (MindNode format **version 9**).
This server reads/writes that plist with Python's standard `plistlib`, so it
needs no third-party plist libraries.

Documents are discovered in the MindNode iCloud container automatically; set
`MINDNODE_DOCS_DIR` to override (e.g. a local library or a test fixture).

## Tools

| Tool | Kind | What it does |
|------|------|--------------|
| `list_documents` | read | All `.mindnode` files, newest first |
| `read_document` | read | Mind maps as `{id, text, note?, children?}` trees, plus `connections` |
| `search_nodes` | read | Substring search over node text + notes, one or all docs |
| `add_node` | write | Add a node under a parent (by id or text) |
| `add_connection` | write | Cross-link two existing nodes (free line), with label + arrow direction |
| `add_tag` / `remove_tag` | write | Tag / untag a node (tags are document-wide, auto-created) |
| `set_task` | write | Turn a node into a checkbox task; set done/todo |
| `attach_image` | write | Attach a local image to a node (copied into `resources/`) |
| `create_map` | write | Make a new `.mindnode` from a title + outline |

### Connections (cross-links)

Stored at `canvas.crossConnections[]`, independent of the parent/child tree.
`add_connection(document, start, end, label?, direction?)` links two existing
nodes referenced by id or text. `direction` ∈ `forward` (default) / `backward`
/ `both` / `none`. `read_document` returns each as
`{id, start_id, end_id, start_text, end_text, direction, label?}`.

### Tags, tasks, attachments

`read_document` surfaces these per node (and lists all tag names at the top):

- **Tags** — normalized: `canvas.tags[]` holds `{tagID, name, color}`,
  `node.tags[]` references tagIDs. `add_tag` auto-defines a tag of that name if
  it doesn't exist, then attaches it (idempotent). Each node shows `tags: [...]`.
- **Tasks** — `node.task = {state, uuids}`; `state` 1 = todo, 2 = done.
  `set_task(document, node, done)` toggles it. Each node shows `task: "todo"|"done"`.
- **Attachments (images)** — `node.attachment = {fileName, size, tintKind, type=2}`,
  bytes stored in `resources/<fileName>`. `attach_image` copies the file in and
  links it, clamping display width to 300px (matching MindNode). Each node shows
  `attachment: "<fileName>"`.

## Write safety

Mutating the user's real files, so every write:

- backs up `contents.xml` to a timestamped `.bak-*` first,
- writes to a temp file then atomically replaces (no partial writes),
- preserves keys we didn't author (styling, layout, print info),
- drops the stale QuickLook preview so it regenerates.

`create_map` clones an existing document as a structural skeleton (keeping all
opaque auxiliary files valid) and overwrites only the node tree.

**Caveat — document open in MindNode:** writes go straight to the file on disk.
If MindNode (or another device via iCloud) has the same document open, its next
autosave can clobber the change, or you may get an iCloud conflict copy. Close
the document in MindNode before writing, or reopen it afterwards to pick up the
edit. (Backups make this recoverable, but it's cleaner to avoid.)

## Run

```bash
uv sync
uv run mindnode-mcp        # stdio MCP server
```

Register with Claude Code:

```bash
claude mcp add mindnode -- uv --directory ~/apps/mindnode-mcp run mindnode-mcp
```

## Status

- [x] Phase 1 — read (list / read / search)
- [x] Phase 2 — write (add_node / create_map)
- [x] Phase 3 — connections / cross-links (read + add_connection)
- [x] Phase 4 — tags, tasks, image attachments (read + write)
