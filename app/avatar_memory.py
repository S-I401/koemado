"""アバター自身の長期記憶（感情・表情・心情・出来事）を SQLite に持つ。

- emotion_log : 応答ごとの表情タグを自動記録（LLM 呼び出しなし。on_finish フックから）
- memories    : LLM が remember ツールで残す記憶（出来事・心情・ユーザーのこと・約束）
- 検索は FTS5 trigram（3文字以上）と LIKE（2文字以下）の併用
- システムプロンプトへの差し込みは context_id ごとに固定（LLM の会話キャッシュを壊さないため）
"""

import re
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from aiavatar.sts.llm import Tool

FACES = ("neutral", "joy", "angry", "sorrow", "fun", "surprised")
FACE_JA = {
    "neutral": "落ち着いている", "joy": "嬉しい", "angry": "むっとしている",
    "sorrow": "少し寂しい・悲しい", "fun": "楽しい", "surprised": "驚いている",
}
KINDS = ("episode", "feeling", "user", "promise")
KIND_JA = {"episode": "出来事", "feeling": "自分の気持ち", "user": "相手のこと", "promise": "約束"}
FACE_TAG = re.compile(r"\[face:([a-zA-Z_]+)\]")
CONTROL_TAG = re.compile(r"\[(?:face|animation):[^\]]*\]")

SCHEMA = """
CREATE TABLE IF NOT EXISTS emotion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id TEXT,
    context_id TEXT,
    face TEXT NOT NULL,
    user_text TEXT,
    avatar_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_emotion_log_user_ts ON emotion_log(user_id, ts);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    user_id TEXT,
    context_id TEXT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    emotion TEXT,
    importance INTEGER NOT NULL DEFAULT 3,
    recall_count INTEGER NOT NULL DEFAULT 0,
    last_recalled_at REAL
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, importance, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, content='memories', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class AvatarMemory:
    def __init__(self, db_path: str, prompt_memories: int = 8, mood_window_hours: float = 24, max_snapshots: int = 100):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self.prompt_memories = prompt_memories
        self.mood_window = mood_window_hours * 3600
        self.max_snapshots = max_snapshots
        self._snapshots: dict[str, str] = {}
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    # ---- 書き込み ----

    def log_turn(self, user_id: str, context_id: str, user_text: str, avatar_text: str):
        faces = [f.lower() for f in FACE_TAG.findall(avatar_text or "") if f.lower() in FACES]
        # 表情タグが無い応答は neutral として数える
        face = next((f for f in reversed(faces) if f != "neutral"), faces[-1] if faces else "neutral")
        with self._connect() as con:
            con.execute(
                "INSERT INTO emotion_log(ts, user_id, context_id, face, user_text, avatar_text) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), user_id, context_id, face, user_text, CONTROL_TAG.sub("", avatar_text or "")),
            )

    def add_memory(self, user_id: str, context_id: str, kind: str, content: str, emotion: str = None, importance: int = 3) -> int:
        kind = kind if kind in KINDS else "episode"
        emotion = emotion if emotion in FACES else None
        importance = min(5, max(1, int(importance or 3)))
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO memories(created_at, user_id, context_id, kind, content, emotion, importance) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), user_id, context_id, kind, content.strip(), emotion, importance),
            )
            return cur.lastrowid

    # ---- 読み出し ----

    def current_mood(self, user_id: str) -> tuple[str, dict]:
        since = time.time() - self.mood_window
        with self._connect() as con:
            rows = con.execute(
                "SELECT face, ts FROM emotion_log WHERE user_id = ? AND ts >= ? ORDER BY ts DESC LIMIT 200",
                (user_id, since),
            ).fetchall()
        if not rows:
            return "neutral", {}
        # 新しいものほど重く数える（半減期 6 時間）
        now = time.time()
        score = Counter()
        for face, ts in rows:
            score[face] += 0.5 ** ((now - ts) / 21600)
        non_neutral = {f: s for f, s in score.items() if f != "neutral"}
        mood = max(non_neutral, key=non_neutral.get) if non_neutral else "neutral"
        return mood, dict(score)

    def top_memories(self, user_id: str) -> list[tuple]:
        # 重要度と新しさで並べる（30 日で重要度 1 段ぶん下がる）
        with self._connect() as con:
            return con.execute(
                "SELECT kind, content, emotion, created_at FROM memories WHERE user_id = ? "
                "ORDER BY importance - (julianday('now') - julianday(created_at, 'unixepoch')) / 30.0 DESC LIMIT ?",
                (user_id, self.prompt_memories),
            ).fetchall()

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        terms = [t for t in query.split() if t] or [query]
        long_terms = [t for t in terms if len(t) >= 3]
        short_terms = [t for t in terms if len(t) < 3]
        sql = "SELECT m.id, m.kind, m.content, m.emotion, m.created_at FROM memories m "
        params: list = []
        where = ["m.user_id = ?"]
        if long_terms:
            sql += "JOIN memories_fts f ON f.rowid = m.id "
            where.append("memories_fts MATCH ?")
            params.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in long_terms))
        for t in short_terms:
            where.append("m.content LIKE ?")
            params.append(f"%{t}%")
        sql += "WHERE " + " AND ".join(where) + " ORDER BY m.importance DESC, m.created_at DESC LIMIT ?"
        with self._connect() as con:
            rows = con.execute(sql, [user_id] + params + [limit]).fetchall()
            if rows:
                con.executemany(
                    "UPDATE memories SET recall_count = recall_count + 1, last_recalled_at = ? WHERE id = ?",
                    [(time.time(), r[0]) for r in rows],
                )
        return [
            {"kind": KIND_JA.get(k, k), "content": c, "emotion": e, "date": _fmt_date(ts)}
            for _, k, c, e, ts in rows
        ]

    def prompt_section(self, user_id: str, context_id: str) -> str:
        """会話（context_id）の最初に一度だけ作り、その会話の間は同じ文字列を返す"""
        key = f"{user_id}:{context_id}"
        if key in self._snapshots:
            return self._snapshots[key]

        mood, _ = self.current_mood(user_id)
        lines = [f"- 今の気分: {FACE_JA.get(mood, mood)}（最近の会話から）"]
        for kind, content, emotion, ts in self.top_memories(user_id):
            feel = f"、そのとき{FACE_JA[emotion]}" if emotion in FACE_JA else ""
            lines.append(f"- [{KIND_JA.get(kind, kind)} {_fmt_date(ts)}{feel}] {content}")
        text = "\n".join(lines)

        if len(self._snapshots) >= self.max_snapshots:
            self._snapshots.pop(next(iter(self._snapshots)))
        self._snapshots[key] = text
        return text

    # ---- ツール ----

    def create_tools(self) -> list[Tool]:
        async def remember(content: str, kind: str = "episode", emotion: str = None, importance: int = 3, metadata: dict = None):
            metadata = metadata or {}
            memory_id = self.add_memory(metadata.get("user_id"), metadata.get("context_id"), kind, content, emotion, importance)
            # 返事を話したあとに呼ばれると、同じ反応をもう一度話しがちなので明示する
            return {
                "saved": True,
                "id": memory_id,
                "instruction": "記録した。このターンですでにユーザーへ返事をしているなら、何も出力せずに終える。まだなら返事を1回だけ話す。",
            }

        async def recall(query: str, metadata: dict = None):
            hits = self.search((metadata or {}).get("user_id"), query)
            return {"results": hits} if hits else {"results": [], "note": "該当する記憶はなかった"}

        remember_tool = Tool(
            name="remember",
            spec={
                "type": "function",
                "function": {
                    "name": "remember",
                    "description": (
                        "あなた自身の記憶として、あとで思い出したいことを1件残す。"
                        "相手が教えてくれた好みや出来事、あなたがそのとき感じたこと、交わした約束など、"
                        "次に話すときにも覚えていたいことがあったときだけ使う。あいさつや相づちには使わない。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "覚えておく内容。あとで読んで分かるように一文で具体的に書く。"},
                            "kind": {"type": "string", "enum": list(KINDS), "description": "episode=出来事, feeling=自分の気持ち, user=相手のこと, promise=約束"},
                            "emotion": {"type": "string", "enum": list(FACES), "description": "そのときのあなたの感情"},
                            "importance": {"type": "integer", "minimum": 1, "maximum": 5, "description": "大切さ。5 がいちばん大切"},
                        },
                        "required": ["content", "kind"],
                    },
                },
            },
            func=remember,
        )
        recall_tool = Tool(
            name="recall",
            spec={
                "type": "function",
                "function": {
                    "name": "recall",
                    "description": "あなた自身の記憶を探す。前に話したことや約束を聞かれ、「記憶」の節だけでは分からないときに使う。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "探すキーワード。複数ある場合は空白で区切る（すべて含むものを探す）。"},
                        },
                        "required": ["query"],
                    },
                },
            },
            func=recall,
        )
        return [remember_tool, recall_tool]
