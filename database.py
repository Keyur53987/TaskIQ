"""
database.py - Persistence layer for the AI Project Manager Assistant.

Manages all SQLite interactions for task CRUD operations.
Uses Python's built-in sqlite3 module with Row factory for dict-like access.
"""

import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "tasks.db"


def get_db_connection() -> sqlite3.Connection:
    """Create and return a new database connection with Row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the database schema. Creates the tasks table if it doesn't exist."""
    try:
        conn = get_db_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                due_date TEXT,
                owner TEXT,
                priority TEXT CHECK(priority IN ('High', 'Medium', 'Low')) DEFAULT 'Medium',
                status TEXT CHECK(status IN ('To Do', 'In Progress', 'Done')) DEFAULT 'To Do'
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def get_tasks(
    owner: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> list[dict]:
    """
    Retrieve tasks from the database with optional filtering.

    Args:
        owner: Filter by owner name (partial, case-insensitive match).
        status: Filter by exact status ('To Do', 'In Progress', 'Done').
        priority: Filter by exact priority ('High', 'Medium', 'Low').

    Returns:
        A list of task dictionaries.
    """
    conn = get_db_connection()
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []

    if owner:
        query += " AND owner LIKE ? COLLATE NOCASE"
        params.append(f"%{owner}%")
    if status:
        query += " AND status = ?"
        params.append(status)
    if priority:
        query += " AND priority = ?"
        params.append(priority)

    query += " ORDER BY id DESC"

    try:
        tasks = conn.execute(query, params).fetchall()
        logger.debug(f"Fetched {len(tasks)} tasks with filters: owner={owner}, status={status}, priority={priority}")
        return [dict(task) for task in tasks]
    except sqlite3.Error as e:
        logger.error(f"Failed to fetch tasks: {e}")
        return []
    finally:
        conn.close()


def create_tasks(tasks_data: list[dict]) -> list[dict]:
    """
    Insert multiple tasks into the database.

    Args:
        tasks_data: A list of task dictionaries with keys:
                    description, due_date, owner, priority, status.

    Returns:
        A list of the newly created task dictionaries (with assigned IDs).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    inserted_tasks: list[dict] = []

    try:
        for t in tasks_data:
            cursor.execute(
                "INSERT INTO tasks (description, due_date, owner, priority, status) VALUES (?, ?, ?, ?, ?)",
                (
                    t.get("description", ""),
                    t.get("due_date"),
                    t.get("owner"),
                    t.get("priority", "Medium"),
                    t.get("status", "To Do"),
                ),
            )
            task_id = cursor.lastrowid
            inserted_tasks.append({
                "id": task_id,
                "description": t.get("description", ""),
                "due_date": t.get("due_date"),
                "owner": t.get("owner"),
                "priority": t.get("priority", "Medium"),
                "status": t.get("status", "To Do"),
            })
        conn.commit()
        logger.info(f"Successfully inserted {len(inserted_tasks)} tasks.")
    except sqlite3.Error as e:
        conn.rollback()
        logger.error(f"Failed to insert tasks: {e}")
        raise
    finally:
        conn.close()

    return inserted_tasks


def get_task(task_id: int) -> Optional[dict]:
    """
    Retrieve a single task by its ID.

    Args:
        task_id: The integer ID of the task.

    Returns:
        A task dictionary, or None if not found.
    """
    conn = get_db_connection()
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(task) if task else None
    except sqlite3.Error as e:
        logger.error(f"Failed to fetch task {task_id}: {e}")
        return None
    finally:
        conn.close()


def update_task(task_id: int, task_data: dict) -> Optional[dict]:
    """
    Update a task's fields by its ID.

    Args:
        task_id: The integer ID of the task to update.
        task_data: A dictionary of fields to update.

    Returns:
        The updated task dictionary, or None if the task was not found.
    """
    allowed_fields = {"description", "due_date", "owner", "priority", "status"}
    conn = get_db_connection()

    set_clause: list[str] = []
    params: list = []

    for k, v in task_data.items():
        if k in allowed_fields:
            set_clause.append(f"{k} = ?")
            params.append(v)

    if not set_clause:
        logger.warning(f"Update called for task {task_id} with no valid fields.")
        return get_task(task_id)

    params.append(task_id)
    query = f"UPDATE tasks SET {', '.join(set_clause)} WHERE id = ?"

    try:
        conn.execute(query, params)
        conn.commit()
        logger.info(f"Updated task {task_id}: {task_data}")
    except sqlite3.Error as e:
        conn.rollback()
        logger.error(f"Failed to update task {task_id}: {e}")
        raise
    finally:
        conn.close()

    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    """
    Delete a task by its ID.

    Args:
        task_id: The integer ID of the task to delete.

    Returns:
        True if a task was deleted, False if no task was found.
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"Deleted task {task_id}.")
        else:
            logger.warning(f"Attempted to delete non-existent task {task_id}.")
        return deleted
    except sqlite3.Error as e:
        conn.rollback()
        logger.error(f"Failed to delete task {task_id}: {e}")
        raise
    finally:
        conn.close()
