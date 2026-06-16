import sqlite3
import time
from pathlib import Path


DB_PATH = Path("agent_memory.sqlite3")


def connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db():
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS channel_preferences (
                channel_id TEXT PRIMARY KEY,
                tone TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_channel_created
            ON messages (channel_id, created_at);

            CREATE TABLE IF NOT EXISTS user_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_user_memories_user_created
            ON user_memories (user_id, created_at);

            CREATE TABLE IF NOT EXISTS channel_summaries (
                channel_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                message_count_at_update INTEGER NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )


def set_channel_tone(channel_id, tone):
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO channel_preferences (channel_id, tone, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                tone = excluded.tone,
                updated_at = excluded.updated_at
            """,
            (str(channel_id), tone, time.time()),
        )


def get_channel_tone(channel_id):
    with connect() as connection:
        row = connection.execute(
            "SELECT tone FROM channel_preferences WHERE channel_id = ?",
            (str(channel_id),),
        ).fetchone()

    if row is None:
        return None

    return row["tone"]


def add_message(channel_id, speaker, content):
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO messages (channel_id, speaker, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(channel_id), speaker, content, time.time()),
        )


def get_message_count(channel_id):
    with connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE channel_id = ?",
            (str(channel_id),),
        ).fetchone()

    return row["count"]


def get_recent_messages(channel_id, limit):
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT speaker, content
            FROM messages
            WHERE channel_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (str(channel_id), limit),
        ).fetchall()

    return [
        {
            "speaker": row["speaker"],
            "content": row["content"],
        }
        for row in reversed(rows)
    ]


def clear_channel_messages(channel_id):
    with connect() as connection:
        connection.execute(
            "DELETE FROM messages WHERE channel_id = ?",
            (str(channel_id),),
        )
        connection.execute(
            "DELETE FROM channel_summaries WHERE channel_id = ?",
            (str(channel_id),),
        )


def add_user_memory(user_id, username, content):
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO user_memories (user_id, username, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(user_id), username, content, time.time()),
        )


def get_user_memories(user_id, limit=8):
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT content
            FROM user_memories
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (str(user_id), limit),
        ).fetchall()

    return [row["content"] for row in reversed(rows)]


def clear_user_memories(user_id):
    with connect() as connection:
        connection.execute(
            "DELETE FROM user_memories WHERE user_id = ?",
            (str(user_id),),
        )


def get_channel_summary(channel_id):
    with connect() as connection:
        row = connection.execute(
            """
            SELECT summary, message_count_at_update
            FROM channel_summaries
            WHERE channel_id = ?
            """,
            (str(channel_id),),
        ).fetchone()

    if row is None:
        return None

    return {
        "summary": row["summary"],
        "message_count_at_update": row["message_count_at_update"],
    }


def set_channel_summary(channel_id, summary, message_count_at_update):
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO channel_summaries (
                channel_id,
                summary,
                message_count_at_update,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                summary = excluded.summary,
                message_count_at_update = excluded.message_count_at_update,
                updated_at = excluded.updated_at
            """,
            (str(channel_id), summary, message_count_at_update, time.time()),
        )
