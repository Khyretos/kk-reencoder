"""
rx_db.py

SQLite database module for kk-reencoder.

Provides functions to manage the state of a video re-encoding library.
Stores file metadata, folder configurations, and processing results.
Rows are returned as plain dicts.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set

import rx_model


class _Rows:
    """Fully fetched result of one statement (so no cursor is shared between threads)."""

    def __init__(self, rows, rowcount):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class LockedConnection:
    """One sqlite3 connection shared by the pool threads and the web server: every statement and
    commit runs under one lock and returns its rows already fetched."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()
        self.generation = 0  # goes up with every write, so a cached summary knows when it is stale
        self.cache: Dict[str, Any] = {}

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            if not sql.lstrip()[:6].upper() in ("SELECT", "PRAGMA"):
                self.generation += 1
            return _Rows(cur.fetchall(), cur.rowcount)

    def commit(self):
        with self._lock:
            self._conn.commit()
            self.generation += 1

    def close(self):
        with self._lock:
            self._conn.close()


def connect(path: str) -> "LockedConnection":
    """
    Open a connection to the SQLite database.

    Args:
        path: Path to the database file. Use ':memory:' for an in-memory DB.

    Returns:
        An open sqlite3.Connection object with row_factory set to dict and WAL mode enabled (if not in-memory).
    """
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    
    # Create tables if missing
    conn.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            library TEXT,
            name TEXT,
            priority INTEGER DEFAULT 0,
            paused INTEGER DEFAULT 0,
            encode INTEGER DEFAULT 0,
            PRIMARY KEY (library, name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            library TEXT,
            folder TEXT,
            name TEXT,
            size INTEGER,
            mtime REAL,
            codec TEXT,
            height INTEGER,
            bitrate_kbps INTEGER,
            duration_s REAL,
            status TEXT,
            reason TEXT,
            new_bytes INTEGER,
            vmaf REAL,
            quality INTEGER,
            codec_after TEXT,
            encoded_at TEXT,
            error TEXT,
            updated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS files_by_folder ON files (library, folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS files_by_status ON files (status)")
    # databases from before the encode flag get the column
    if "encode" not in [r["name"] for r in conn.execute("PRAGMA table_info(folders)").fetchall()]:
        conn.execute("ALTER TABLE folders ADD COLUMN encode INTEGER DEFAULT 0")
        conn.commit()
    return LockedConnection(conn)


def upsert_file(conn: sqlite3.Connection, rec: Dict[str, Any]) -> None:
    """
    Insert or update a file record.

    Args:
        conn: Database connection.
        rec: Dictionary containing file metadata. Must have 'path', 'folder', 'size', 'mtime'.
             Other fields like 'codec', 'status', etc., are optional but recommended.
    """
    path = rec["path"]
    folder = rec["folder"]
    size = rec["size"]
    mtime = rec["mtime"]
    
    # Ensure folder exists
    conn.execute(
        "INSERT OR IGNORE INTO folders (library, name) VALUES (?, ?)",
        (rec["library"], folder)
    )
    
    # Determine if this is a new path or an existing one
    cursor = conn.execute("SELECT * FROM files WHERE path = ?", (path,))
    row = cursor.fetchone()
    
    if row is None:
        # New path: insert
        name = PurePosixPath(path).name
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO files (path, library, folder, name, size, mtime, codec, height, 
                              bitrate_kbps, duration_s, status, reason, new_bytes, vmaf, 
                              quality, codec_after, encoded_at, error, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?)
        """, (
            path, rec["library"], folder, name, size, mtime,
            rec.get("codec"), rec.get("height"), rec.get("bitrate_kbps"), rec.get("duration_s"),
            rec.get("status", "pending"), rec.get("reason"),
            now
        ))
    else:
        # Existing row
        old_status = row["status"]
        
        if old_status in ("encoded", "staged", "confirmed", "running"):
            # the file on disk may have changed because of our own swap: keep the row
            pass
        elif row["size"] == size and row["mtime"] == mtime:
            # file unchanged: keep the result
            pass
        else:
            # File changed: overwrite every field from rec and clear result fields
            name = PurePosixPath(path).name
            now = datetime.now(timezone.utc).isoformat()
            
            # Build values list
            vals = [
                path, rec["library"], folder, name, size, mtime,
                rec.get("codec"), rec.get("height"), rec.get("bitrate_kbps"), rec.get("duration_s"),
                rec.get("status", "pending"), rec.get("reason"),
                None, None, None, None, None, None, now
            ]
            
            cols = [
                "path", "library", "folder", "name", "size", "mtime", "codec", "height",
                "bitrate_kbps", "duration_s", "status", "reason", "new_bytes", "vmaf",
                "quality", "codec_after", "encoded_at", "error", "updated"
            ]
            
            conn.execute(
                f"UPDATE files SET {', '.join(c + ' = ?' for c in cols)} WHERE path = ?",
                tuple(vals) + (path,)
            )
    
    conn.commit()


def get_file(conn: sqlite3.Connection, path: str) -> Optional[Dict[str, Any]]:
    """
    Get a file record by path.

    Args:
        conn: Database connection.
        path: The file path.

    Returns:
        A dictionary with the file data, or None if not found.
    """
    cursor = conn.execute("SELECT * FROM files WHERE path = ?", (path,))
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row)


def set_result(conn: sqlite3.Connection, path: str, **fields: Any) -> None:
    """
    Update result fields for a file.

    Args:
        conn: Database connection.
        path: The file path.
        **fields: Columns to update (must be valid columns in the files table except 'updated').
    """
    valid_cols = {
        "status", "reason", "new_bytes", "vmaf", "quality", "codec_after", 
        "encoded_at", "error"
    }
    updates = []
    values = []
    
    for k, v in fields.items():
        if k in valid_cols:
            updates.append(f"{k} = ?")
            values.append(v)
    
    if updates:
        now = datetime.now(timezone.utc).isoformat()
        updates.append("updated = ?")
        values.append(now)
        
        placeholders = ", ".join(["?" for _ in values])
        sql = f"UPDATE files SET {', '.join(updates)} WHERE path = ?"
        values.append(path)
        
        conn.execute(sql, values)
        conn.commit()


def set_folder(conn: sqlite3.Connection, library: str, name: str, priority: Optional[int] = None, paused: Optional[bool] = None, encode: Optional[bool] = None) -> None:
    """
    Insert or update a folder configuration.

    Args:
        conn: Database connection.
        library: Library name.
        name: Folder name.
        priority: Priority level (optional).
        paused: Paused status (optional, stored as 0/1).
        encode: Encode flag (optional, stored as 0/1).
    """
    # Insert if missing
    conn.execute(
        "INSERT OR IGNORE INTO folders (library, name) VALUES (?, ?)",
        (library, name)
    )
    
    # Update what is not None
    if priority is not None:
        conn.execute(
            "UPDATE folders SET priority = ? WHERE library = ? AND name = ?",
            (priority, library, name)
        )
    
    if paused is not None:
        conn.execute(
            "UPDATE folders SET paused = ? WHERE library = ? AND name = ?",
            (1 if paused else 0, library, name)
        )
    
    if encode is not None:
        # Ensure the column exists before updating
        cursor = conn.execute("PRAGMA table_info(folders)")
        columns = [row["name"] for row in cursor.fetchall()]
        
        if "encode" not in columns:
            conn.execute(
                "ALTER TABLE folders ADD COLUMN encode INTEGER DEFAULT 0"
            )
        
        conn.execute(
            "UPDATE folders SET encode = ? WHERE library = ? AND name = ?",
            (1 if encode else 0, library, name)
        )
        
        # If marking for encoding, reset predicted files to pending
        if encode:
            conn.execute("""
                UPDATE files 
                SET status = 'pending', new_bytes = NULL 
                WHERE library = ? AND folder = ? AND status = 'predicted'
            """, (library, name))
    
    conn.commit()


def folders(conn) -> List[Dict[str, Any]]:
    """One summary per (library, folder) that has files, in ONE grouped query (the page asks every few
    seconds and the library has thousands of files), sorted by name case-insensitive, then library.
    The result is kept until the next write: one status poll used to run this query three times."""
    cache = getattr(conn, "cache", None)
    gen = getattr(conn, "generation", None)
    if cache is not None and cache.get("folders_gen") == gen:
        return [dict(d) for d in cache["folders"]]
    out = _folders(conn)
    if cache is not None:
        cache["folders"], cache["folders_gen"] = out, gen
    return [dict(d) for d in out]


def _folders(conn) -> List[Dict[str, Any]]:
    rows = conn.execute("""
        SELECT f.library AS library, f.folder AS name,
               COALESCE(d.priority, 0) AS priority, COALESCE(d.paused, 0) AS paused, COALESCE(d.encode, 0) AS encode,
               COUNT(*) AS files,
               SUM(f.status = 'pending') AS pending, SUM(f.status = 'running') AS running,
               SUM(f.status = 'encoded') AS encoded, SUM(f.status = 'predicted') AS predicted,
               SUM(f.status = 'skipped') AS skipped, SUM(f.status = 'failed') AS failed,
               SUM(f.status = 'staged') AS staged, SUM(f.status = 'confirmed') AS confirmed,
               COALESCE(SUM(f.size), 0) AS original_bytes,
               COALESCE(SUM(CASE WHEN f.status IN ('encoded', 'confirmed') AND f.new_bytes IS NOT NULL
                                 THEN f.new_bytes ELSE f.size END), 0) AS current_bytes,
               COALESCE(SUM(CASE WHEN f.status IN ('encoded', 'confirmed', 'staged', 'predicted') AND f.new_bytes IS NOT NULL
                                 THEN f.new_bytes ELSE f.size END), 0) AS projected_bytes,
               COALESCE(SUM(CASE WHEN f.status = 'staged' THEN COALESCE(f.new_bytes, 0)
                                 WHEN f.status = 'confirmed' THEN f.size ELSE 0 END), 0) AS extra_bytes
        FROM files f LEFT JOIN folders d ON d.library = f.library AND d.name = f.folder
        GROUP BY f.library, f.folder
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["paused"] = bool(d["paused"])
        d["encode"] = bool(d["encode"])
        out.append(d)
    out.sort(key=lambda x: (x["name"].lower(), x["library"]))
    return out


def next_file(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """
    Get the next pending file to work on.

    Returns:
        The first pending file (by path) of the first folder according to order_folders, or None.
    """
    # Get ordered folders
    ordered_folders = rx_model.order_folders(folders(conn))
    
    if not ordered_folders:
        return None
    
    # Find the first pending file in the first folder
    first_folder = ordered_folders[0]
    lib = first_folder["library"]
    name = first_folder["name"]
    
    cursor = conn.execute("""
        SELECT * FROM files 
        WHERE library = ? AND folder = ? AND status = 'pending'
        ORDER BY path ASC
        LIMIT 1
    """, (lib, name))
    
    row = cursor.fetchone()
    if row is None:
        return None
    
    return dict(row)


def folder_files(conn: sqlite3.Connection, library: str, name: str) -> List[Dict[str, Any]]:
    """
    Get files in a specific folder.

    Args:
        conn: Database connection.
        library: Library name.
        name: Folder name.

    Returns:
        List of file dicts in report shape, sorted by path.
    """
    cursor = conn.execute("""
        SELECT path, name, status, codec, codec_after, size, new_bytes, vmaf, quality, reason, error, encoded_at
        FROM files 
        WHERE library = ? AND folder = ?
        ORDER BY path ASC
    """, (library, name))
    
    result = []
    for row in cursor:
        result.append({
            "path": row["path"],
            "name": row["name"],
            "status": row["status"],
            "codec_before": row["codec"],
            "codec_after": row["codec_after"],
            "original_bytes": row["size"],
            "new_bytes": row["new_bytes"],
            "vmaf": row["vmaf"],
            "quality": row["quality"],
            "reason": row["reason"],
            "error": row["error"],
            "encoded_at": row["encoded_at"]
        })
    
    return result


def get_state(conn: sqlite3.Connection, key: str, default: str) -> str:
    """
    Get a state value.

    Args:
        conn: Database connection.
        key: State key.
        default: Default value if key not found.

    Returns:
        The stored value or default.
    """
    cursor = conn.execute("SELECT value FROM state WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row is None:
        return default
    return row["value"]


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """
    Set a state value.

    Args:
        conn: Database connection.
        key: State key.
        value: Value to store.
    """
    conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def remove_missing(conn: sqlite3.Connection, seen_paths: Set[str]) -> int:
    """
    Delete file rows whose path is not in the given set, except rows with status 'encoded'.

    Args:
        conn: Database connection.
        seen_paths: Set of paths that exist on disk.

    Returns:
        Number of rows deleted.
    """
    # Find paths to delete: not in seen_paths AND status != 'encoded'
    cursor = conn.execute("SELECT path FROM files WHERE status NOT IN ('encoded', 'staged', 'confirmed')")
    paths_to_delete = [row["path"] for row in cursor if row["path"] not in seen_paths]
    
    if paths_to_delete:
        placeholders = ", ".join(["?" for _ in paths_to_delete])
        conn.execute(f"DELETE FROM files WHERE path IN ({placeholders})", paths_to_delete)
        conn.commit()
    
    return len(paths_to_delete)


def staged_bytes(conn: sqlite3.Connection) -> int:
    """Disk used next to the library: staged copies plus originals kept after a confirm."""
    return conn.execute("""
        SELECT COALESCE(SUM(CASE WHEN status = 'staged' THEN COALESCE(new_bytes, 0)
                                 WHEN status = 'confirmed' THEN size ELSE 0 END), 0) AS b FROM files
    """).fetchone()["b"]


def files_with_status(conn: sqlite3.Connection, status: str, library: Optional[str] = None,
                      folder: Optional[str] = None) -> List[Dict[str, Any]]:
    """Rows with this status (optionally of one library / folder), sorted by path."""
    sql, args = "SELECT * FROM files WHERE status = ?", [status]
    if library is not None:
        sql += " AND library = ?"
        args.append(library)
    if folder is not None:
        sql += " AND folder = ?"
        args.append(folder)
    return [dict(r) for r in conn.execute(sql + " ORDER BY path", args)]


def reset_running(conn: sqlite3.Connection) -> int:
    """Put rows left 'running' by a stopped worker back to 'pending'."""
    n = conn.execute("UPDATE files SET status = 'pending' WHERE status = 'running'").rowcount
    conn.commit()
    return n


def folder_encode(conn, library: str, name: str) -> bool:
    """True when the series is marked for re-encoding on the page."""
    row = conn.execute("SELECT encode FROM folders WHERE library = ? AND name = ?", (library, name)).fetchone()
    return bool(row and row["encode"])



def requeue_quality_skips(conn) -> int:
    """Put files the quality search skipped back to pending (after the target or size limit changed)."""
    rows = conn.execute("SELECT path, reason FROM files WHERE status = 'skipped'").fetchall()
    paths = [r["path"] for r in rows if rx_model.is_quality_reason(r["reason"])]
    for p in paths:
        conn.execute("UPDATE files SET status = 'pending', reason = NULL, error = NULL WHERE path = ?", (p,))
    conn.commit()
    return len(paths)


def requeue_reason(conn, reason: str) -> int:
    """Put files skipped for exactly this reason back to pending (a setting that excluded them was turned off)."""
    n = conn.execute("UPDATE files SET status = 'pending', reason = NULL WHERE status = 'skipped' AND reason = ?",
                     (reason,)).rowcount
    conn.commit()
    return n
