"""Read and write MindNode documents at the plist level.

A .mindnode document is a *package* (directory). The node tree lives in
`contents.xml`, which — despite the extension — is an Apple **binary plist**.

Schema (MindNode format version 9):
    {
      "version": 9,
      "typeOptions": {},
      "NSPrintInfo": <opaque>,
      "canvas": {
        "color": "{r, g, b, ...}",
        "mindMaps": [
          {
            "mainNode": <node>,
            "branchType": ..., "layoutStyle": ..., ...
          }
        ]
      }
    }

    node = {
      "nodeID": "<UUID>",
      "title": {"text": "<html>", "allowToShrinkWidth": bool},
      "note": {"text": "<html>"} | absent,
      "subnodes": [<node>, ...],
      "location": "{x, y}",
      "shapeStyle": {...}, "pathStyle": {...},
      "hasFoldedSubnodes": bool, ...
    }

We treat unknown keys as opaque and preserve them on write, so we never lose
styling/layout data we didn't author.
"""

from __future__ import annotations

import html
import plistlib
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

CONTENTS = "contents.xml"
SUPPORTED_VERSIONS = {9}


# --------------------------------------------------------------------------- #
# plist load / save
# --------------------------------------------------------------------------- #
def contents_path(doc: Path) -> Path:
    return doc / CONTENTS


def load_document(doc: Path) -> dict:
    """Load and parse a .mindnode package's contents.xml (binary plist)."""
    cp = contents_path(doc)
    if not cp.is_file():
        raise FileNotFoundError(f"Not a MindNode package (no {CONTENTS}): {doc}")
    with cp.open("rb") as f:
        data = plistlib.load(f)
    version = data.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"Unsupported MindNode format version {version!r} in {doc.name}. "
            f"This server supports version(s): {sorted(SUPPORTED_VERSIONS)}."
        )
    return data


def save_document(doc: Path, data: dict, *, backup: bool = True) -> Path:
    """Write data back to contents.xml as a binary plist, atomically.

    Safety measures, because we are mutating the user's real files:
      - optional timestamped backup of the existing contents.xml
      - write to a temp file in the same directory, then atomic os.replace
      - invalidate the stale QuickLook preview (MindNode regenerates it)
    """
    cp = contents_path(doc)
    if backup and cp.is_file():
        bak = cp.with_suffix(cp.suffix + f".bak-{_stamp()}")
        shutil.copy2(cp, bak)

    tmp = cp.with_suffix(cp.suffix + ".tmp")
    with tmp.open("wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)
    tmp.replace(cp)

    # The cached QuickLook thumbnail is now stale; drop it so Finder/MindNode
    # regenerate from the new contents rather than showing the old image.
    preview = doc / "QuickLook" / "Preview.jpg"
    if preview.is_file():
        try:
            preview.unlink()
        except OSError:
            pass
    return cp


def _stamp() -> str:
    # Local import so the module stays import-time pure for tooling that
    # forbids module-level clock reads.
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------- #
# HTML <-> plain text
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def html_to_text(value: str | None) -> str:
    """Convert MindNode's stored HTML title/note into plain text."""
    if not value:
        return ""
    # Block-ish tags become newlines so multi-paragraph notes survive.
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", value, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def text_to_html(text: str) -> str:
    """Wrap plain text into the minimal HTML MindNode expects for a title/note.

    MindNode renders one <p> per line. We escape user text to avoid breaking
    the markup or injecting attributes.
    """
    lines = text.split("\n")
    return "".join(f"<p>{html.escape(line)}</p>" for line in lines) or "<p></p>"


# --------------------------------------------------------------------------- #
# node tree helpers
# --------------------------------------------------------------------------- #
def node_text(node: dict) -> str:
    title = node.get("title")
    if isinstance(title, dict):
        return html_to_text(title.get("text"))
    return html_to_text(title if isinstance(title, str) else "")


def node_note(node: dict) -> str:
    note = node.get("note")
    if isinstance(note, dict):
        return html_to_text(note.get("text"))
    return html_to_text(note if isinstance(note, str) else "")


def to_tree(node: dict, name_map: dict | None = None) -> dict:
    """Normalize a raw plist node into a clean, JSON-friendly tree.

    Pass `name_map` (tagID -> name, from tag_name_map) to resolve tag names.
    """
    tree: dict = {
        "id": node.get("nodeID"),
        "text": node_text(node),
    }
    note = node_note(node)
    if note:
        tree["note"] = note
    status = task_status_str(node)
    if status:
        tree["task"] = status
    if name_map is not None:
        names = node_tag_names(node, name_map)
        if names:
            tree["tags"] = names
    att = node.get("attachment")
    if isinstance(att, dict) and att.get("fileName"):
        tree["attachment"] = att["fileName"]
    children = node.get("subnodes") or []
    if children:
        tree["children"] = [to_tree(c, name_map) for c in children]
    return tree


def main_nodes(data: dict) -> list[dict]:
    """Return the root node of every mind map in the document."""
    maps = (data.get("canvas") or {}).get("mindMaps") or []
    return [m["mainNode"] for m in maps if isinstance(m, dict) and "mainNode" in m]


def iter_nodes(node: dict):
    """Depth-first iterator over a node and all its descendants."""
    yield node
    for child in node.get("subnodes") or []:
        yield from iter_nodes(child)


def find_node(data: dict, node_id: str) -> dict | None:
    for root in main_nodes(data):
        for n in iter_nodes(root):
            if n.get("nodeID") == node_id:
                return n
    return None


# --------------------------------------------------------------------------- #
# location parsing (stored as "{x, y}")
# --------------------------------------------------------------------------- #
@dataclass
class Point:
    x: float
    y: float

    def __str__(self) -> str:
        return f"{{{self.x}, {self.y}}}"


def parse_location(value: str | None) -> Point | None:
    if not value:
        return None
    nums = re.findall(r"-?\d+(?:\.\d+)?", value)
    if len(nums) < 2:
        return None
    return Point(float(nums[0]), float(nums[1]))


# --------------------------------------------------------------------------- #
# node construction (write path)
# --------------------------------------------------------------------------- #
def new_node_id() -> str:
    return str(uuid.uuid4()).upper()


def make_node(text: str, *, template: dict | None = None, note: str | None = None) -> dict:
    """Build a fresh node, inheriting style from a template sibling/parent.

    Inheriting shapeStyle/pathStyle from an existing node in the same document
    avoids handing MindNode an under-specified node it might choke on.
    """
    node: dict = {
        "nodeID": new_node_id(),
        "title": {"text": text_to_html(text), "allowToShrinkWidth": True},
        "subnodes": [],
        "hasFoldedSubnodes": False,
    }
    if note:
        node["note"] = {"text": text_to_html(note)}
    if template:
        for key in ("shapeStyle", "pathStyle", "attachmentPosition", "styleChangedByUser"):
            if key in template:
                node[key] = template[key]
        loc = parse_location(template.get("location"))
        if loc:
            # Offset below the template so the new node isn't stacked exactly on it.
            node["location"] = str(Point(loc.x, loc.y + 60))
    return node


# --------------------------------------------------------------------------- #
# cross-connections (free links between any two nodes, independent of the tree)
# --------------------------------------------------------------------------- #
# Stored at canvas.crossConnections[]. Defaults captured from real documents so
# we can add a connection even to a file that has none yet (no template to copy).
_DEFAULT_FONT_STYLE = {
    "italic": False,
    "color": "{1.000000, 1.000000, 1.000000, 1.000000}",
    "fontSize": 14,
    "strikethrough": False,
    "fontName": "HelveticaNeue",
    "alignment": 1,
    "underline": False,
    "bold": False,
}
_DEFAULT_STROKE_STYLE = {
    "color": "{0.366349, 0.366358, 0.366353, 1.000000}",
    "width": 1,
    "dash": 1,
}
# arrowStyle is a pair of (startArrow, endArrow); 0 = none, 1 = arrowhead.
_ARROW = {
    "forward": {"startArrow": 0, "endArrow": 1},
    "backward": {"startArrow": 1, "endArrow": 0},
    "both": {"startArrow": 1, "endArrow": 1},
    "none": {"startArrow": 0, "endArrow": 0},
}
CONNECTION_DIRECTIONS = tuple(_ARROW)


def cross_connections(data: dict) -> list[dict]:
    return (data.get("canvas") or {}).get("crossConnections") or []


def connection_label(conn: dict) -> str:
    title = conn.get("title")
    if isinstance(title, dict):
        return html_to_text(title.get("text"))
    return ""


def connection_direction(conn: dict) -> str:
    a = conn.get("arrowStyle") or {}
    key = (a.get("startArrow", 0), a.get("endArrow", 0))
    for name, style in _ARROW.items():
        if (style["startArrow"], style["endArrow"]) == key:
            return name
    return "forward"


def connection_to_dict(conn: dict, data: dict) -> dict:
    """Normalize a raw crossConnection into a JSON-friendly summary."""
    ep = conn.get("endPoints") or {}
    sid, eid = ep.get("startNodeID"), ep.get("endNodeID")
    start, end = find_node(data, sid), find_node(data, eid)
    out: dict = {
        "id": conn.get("connectionID"),
        "start_id": sid,
        "end_id": eid,
        "start_text": node_text(start) if start else None,
        "end_text": node_text(end) if end else None,
        "direction": connection_direction(conn),
    }
    label = connection_label(conn)
    if label:
        out["label"] = label
    return out


def make_connection(
    start_id: str,
    end_id: str,
    *,
    label: str | None = None,
    direction: str = "forward",
    template: dict | None = None,
) -> dict:
    """Build a fresh crossConnection between two node ids.

    Inherits stroke/font style from an existing connection (template) when the
    document already has one; otherwise falls back to defaults sampled from real
    MindNode files. wayPoints are left empty so MindNode draws a straight line.
    """
    conn: dict = {
        "connectionID": new_node_id(),
        "endPoints": {"startNodeID": start_id, "endNodeID": end_id},
        "arrowStyle": dict(_ARROW.get(direction, _ARROW["forward"])),
        "layout": {"wayPoints": []},
    }
    if template and isinstance(template.get("pathStyle"), dict):
        conn["pathStyle"] = template["pathStyle"]
    else:
        conn["pathStyle"] = {"strokeStyle": dict(_DEFAULT_STROKE_STYLE)}
    if label:
        font = None
        if template and isinstance(template.get("title"), dict):
            font = template["title"].get("fontStyle")
        conn["title"] = {
            "text": text_to_html(label),
            "fontStyle": font or dict(_DEFAULT_FONT_STYLE),
            "maxWidth": 250,
            "allowToShrinkWidth": True,
        }
    return conn


# --------------------------------------------------------------------------- #
# tags (normalized: canvas.tags[] defines {tagID,name,color}; node.tags[] refs)
# --------------------------------------------------------------------------- #
_DEFAULT_TAG_COLOR = "{1.000000, 0.372549, 0.411765, 1.000000}"


def canvas_tags(data: dict) -> list[dict]:
    return (data.get("canvas") or {}).get("tags") or []


def tag_name_map(data: dict) -> dict[str, str]:
    """tagID -> name, for resolving node.tags into human-readable names."""
    return {
        t.get("tagID"): t.get("name")
        for t in canvas_tags(data)
        if isinstance(t, dict) and t.get("tagID")
    }


def node_tag_names(node: dict, name_map: dict[str, str]) -> list[str]:
    return [name_map[tid] for tid in (node.get("tags") or []) if tid in name_map]


def ensure_tag(data: dict, name: str, color: str | None = None) -> str:
    """Return the tagID for `name`, defining it in canvas.tags if absent."""
    canvas = data.setdefault("canvas", {})
    tags = canvas.setdefault("tags", [])
    for t in tags:
        if isinstance(t, dict) and t.get("name") == name:
            return t["tagID"]
    tag_id = new_node_id()
    tags.append({"tagID": tag_id, "name": name, "color": color or _DEFAULT_TAG_COLOR})
    return tag_id


def add_tag_to_node(node: dict, tag_id: str) -> bool:
    """Attach a tagID to a node. Returns False if it was already present."""
    tags = node.setdefault("tags", [])
    if tag_id in tags:
        return False
    tags.append(tag_id)
    return True


def remove_tag_from_node(node: dict, tag_id: str) -> bool:
    tags = node.get("tags") or []
    if tag_id not in tags:
        return False
    node["tags"] = [t for t in tags if t != tag_id]
    if not node["tags"]:
        node.pop("tags", None)
    return True


# --------------------------------------------------------------------------- #
# task (checkbox): node.task = {"state": 1=todo | 2=done, "uuids": {}}
# --------------------------------------------------------------------------- #
TASK_TODO = 1
TASK_DONE = 2


def task_status_str(node: dict) -> str | None:
    t = node.get("task")
    if not isinstance(t, dict):
        return None
    return "done" if t.get("state") == TASK_DONE else "todo"


def set_node_task(node: dict, done: bool) -> None:
    node["task"] = {"state": TASK_DONE if done else TASK_TODO, "uuids": {}}


def clear_node_task(node: dict) -> bool:
    return node.pop("task", None) is not None


# --------------------------------------------------------------------------- #
# attachment (image): node.attachment = {fileName, size, tintKind, type=2};
# the image bytes live in <package>/resources/<fileName>.
# --------------------------------------------------------------------------- #
ATTACHMENT_IMAGE = 2
ATTACHMENT_MAX_DISPLAY_WIDTH = 300


def node_attachment(node: dict) -> dict | None:
    a = node.get("attachment")
    if isinstance(a, dict) and a.get("fileName"):
        return {"fileName": a["fileName"], "type": a.get("type")}
    return None


def make_image_attachment(file_name: str, width: float, height: float) -> dict:
    """Build an image attachment dict, clamping display width like MindNode."""
    if width and width > ATTACHMENT_MAX_DISPLAY_WIDTH:
        height = round(height * ATTACHMENT_MAX_DISPLAY_WIDTH / width, 6)
        width = ATTACHMENT_MAX_DISPLAY_WIDTH
    return {
        "fileName": file_name,
        "size": str(Point(float(width or ATTACHMENT_MAX_DISPLAY_WIDTH), float(height or 200))),
        "tintKind": 0,
        "type": ATTACHMENT_IMAGE,
    }
