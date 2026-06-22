# mindnode-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

[English](README.md) | **日本語**

AI アシスタント（Claude など）が、あなたの [MindNode](https://mindnode.com)
のマインドマップを **直接読み書きできる** [MCP](https://modelcontextprotocol.io)
サーバーです。`.mindnode` ファイルのフォーマット自体を解析するので、AppleScript
も Shortcuts も、書き出し/取り込みの手間も要りません。

「プロジェクトのマップ読んで」「Marketing の下にこの3つのアイデアを足して」
「API ノードと Auth ノードをつないで」「この箇条書きを新しいマップにして」——
こう頼むだけで、MindNode が同期している実ファイルを操作します。

> **なぜ作ったか:** MindNode は AppleScript 辞書を廃止し、Shortcuts / URL
> スキームによる自動化も貧弱です。しかし `.mindnode` ドキュメントは、中の
> `contents.xml` が Apple の **バイナリ plist**（きれいな再帰的ノードツリー）に
> なっているパッケージにすぎません。これを読み書きすれば、プログラムから完全に
> 制御できます。

## 必要なもの

- **macOS** ＋ [MindNode](https://mindnode.com) インストール済み（フォーマット
  version 9 — 現行の MindNode）
- **Python 3.11+** と [uv](https://github.com/astral-sh/uv)
- MCP クライアント（例: [Claude Code](https://claude.com/claude-code)）

## インストール

```bash
git clone https://github.com/masamitsu-konya/mindnode-mcp.git
cd mindnode-mcp
uv sync
```

Claude Code に登録（user スコープ＝どこからでも使える）:

```bash
claude mcp add --scope user mindnode -- uv --directory "$PWD" run mindnode-mcp
```

新しいセッションを開いて `/mcp`（または `claude mcp list`）を実行し、
**mindnode ✔ Connected** と表示されれば成功です。

デフォルトでは iCloud コンテナ内の MindNode ドキュメントを自動検出します。別の
場所を読ませたい場合は環境変数 `MINDNODE_DOCS_DIR` で指定してください（ローカル
ライブラリやテスト用フォルダなど）。

## 使い方

普通の言葉で頼めば、アシスタントが適切なツールを選びます。例:

| こう言うと | こう動く |
|-----------|---------|
| 「マインドマップ一覧見せて」 | `list_documents` → 名前と更新日（新しい順） |
| 「*プロジェクト計画* のマップ読んで」 | `read_document` → ノードツリー全体を JSON で |
| 「全マップから "pricing" を検索して」 | `search_nodes` → 一致したノードとそのドキュメント |
| 「*Roadmap* の Team ノードの下に 'デザイナー採用' を足して」 | `add_node` |
| 「'Frontend' と 'API' を 'calls' ラベルでつないで」 | `add_connection` |
| 「'Launch' ノードに #urgent タグを付けて」 | `add_tag`（無ければ自動作成） |
| 「'Ship v1' を完了にして」 | `set_task` |
| 「~/Desktop/wireframe.png を Design ノードに添付して」 | `attach_image` |
| 「Sales / Product / Hiring の枝を持つ 'Q3 Goals' マップを作って」 | `create_map` |

ノードはテキスト（大文字小文字を無視した部分一致でOK）でも、正確な id（`read_document`
の結果に含まれる）でも指定できます。

## ツール一覧

| ツール | 種別 | 内容 |
|--------|------|------|
| `list_documents` | 読 | 全 `.mindnode` ファイル（新しい順） |
| `read_document` | 読 | マップを `{id, text, note?, task?, tags?, attachment?, children?}` のツリーで返す。`connections` とドキュメントのタグ一覧も |
| `search_nodes` | 読 | ノード本文＋ノートの部分一致検索（1ドキュメント or 全件） |
| `add_node` | 書 | 親（id or テキスト）の下にノード追加。ノートも任意で |
| `add_connection` | 書 | 2つの既存ノードをクロスリンク（ラベル・矢印の向き指定可） |
| `add_tag` / `remove_tag` | 書 | ノードにタグ付け/解除（タグはドキュメント全体で共有・自動作成） |
| `set_task` | 書 | ノードをチェックボックス化し done / todo を設定 |
| `attach_image` | 書 | ローカル画像をノードに添付（パッケージの `resources/` にコピー） |
| `create_map` | 書 | タイトル＋（入れ子可の）アウトラインから新規 `.mindnode` を作成 |

### connection（クロスリンク）

親子ツリーとは独立した、任意の2ノード間を結ぶ自由な線（`canvas.crossConnections[]`
に格納）。`add_connection(document, start, end, label?, direction?)` —
`direction` は `forward`（デフォルト）/ `backward` / `both` / `none`。
`read_document` は各 connection を `{id, start_id, end_id, start_text,
end_text, direction, label?}` で返します。

### タグ・タスク・添付

`read_document` はこれらをノードごとに返します（先頭に全タグ名も）:

- **タグ** — 正規化モデル: `canvas.tags[]` が `{tagID, name, color}` を定義し、
  `node.tags[]` が tagID を参照。`add_tag` は同名タグが無ければ自動定義してから
  付与（冪等）。
- **タスク** — `node.task = {state, uuids}`、`state` は 1=todo / 2=done。
  `set_task(document, node, done)` で切り替え。
- **添付（画像）** — `node.attachment = {fileName, size, tintKind, type}`、
  画像本体は `resources/<fileName>`。`attach_image` がファイルをコピーして
  リンクし、表示幅を 300px にクランプ（MindNode の挙動に合わせる）。

## 仕組み

`.mindnode` ドキュメントは **パッケージ（ディレクトリ）** です。中の
`contents.xml` は拡張子に反して Apple の **バイナリ plist** で、マインドマップ
（フォーマット **version 9**）を保持しています:

```
canvas.mindMaps[].mainNode        # 各マップのルートノード
  ├─ nodeID                       # UUID
  ├─ title.text                   # ノードのテキスト（小さな HTML で格納）
  ├─ note / task / tags / attachment
  └─ subnodes[]                   # 子ノード（同じ形・再帰）
canvas.crossConnections[]         # ノード間の自由な線
canvas.tags[]                     # タグ定義
```

これを Python 標準の `plistlib` で読み書きするため、**サードパーティの plist
ライブラリは不要**です。ノードのテキストは最小限の HTML エンコード/デコードで
往復します（適切にエスケープ）。

## 書き込みの安全設計

実ファイルを変更するため、すべての書き込みで:

- まず `contents.xml` をタイムスタンプ付き `.bak-*` に **バックアップ**、
- 一時ファイルに書いてから **atomic に置換**（部分書き込みなし）、
- **自分が書いていないキーを保全**（スタイル・レイアウト・印刷情報）、
- 古い QuickLook プレビューを削除（再生成させる）。

`create_map` は既存ドキュメントを構造の雛形として複製し（不可視の補助ファイルを
すべて有効に保つ）、ノードツリーだけを上書きします。

> **注意 — MindNode でドキュメントを開いている場合。** 書き込みはディスクへ直接
> 行われます。MindNode（または iCloud 経由の別デバイス）が同じドキュメントを開いて
> いると、次の自動保存で変更が上書きされたり、iCloud の競合コピーが生じることが
> あります。書き込み前に MindNode 側で閉じるか、書き込み後に開き直して反映して
> ください。バックアップがあるので復旧は可能ですが、避けた方がきれいです。

## 開発

```bash
uv run python tests/smoke.py
```

スモークテストは、読み取りは実ドキュメントに対して（読み取り専用）、書き込みは
すべて使い捨ての一時コピーに対して実行します——実際のマップは一切変更しません。
さらに、生成した構造（ノード・connection・タグ・タスク・画像添付）が実 MindNode
ファイルのスキーマとキー単位で一致することも検証します。

## ステータス & ロードマップ

- [x] 読み取り — list / read / search
- [x] 書き込み — add_node / create_map
- [x] connection / クロスリンク — read + add_connection
- [x] タグ・タスク・画像添付 — read + write
- [ ] 画像以外の添付（リンク・ステッカー）
- [ ] タグの色パレット / リネーム、タスク削除
- [ ] connection の waypoint 編集

## ライセンス

[MIT](LICENSE) © 2026 Masamitsu Konya
