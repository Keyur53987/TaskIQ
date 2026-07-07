import pytest
from fastapi.testclient import TestClient
import os
import sys

# Add the parent directory to sys.path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# We patch the database path before importing the app
import database
database.DB_PATH = "test_tasks.db"

from main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    """Setup and teardown the test database."""
    # Ensure a fresh db for each test
    if os.path.exists(database.DB_PATH):
        os.remove(database.DB_PATH)
    
    database.init_db()
    yield
    
    # Cleanup after test
    if os.path.exists(database.DB_PATH):
        os.remove(database.DB_PATH)

def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_create_and_get_tasks():
    # Insert a task directly via db to test the get endpoint
    database.create_tasks([
        {"description": "Test Task 1", "priority": "High", "status": "To Do"}
    ])
    
    response = client.get("/api/tasks")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["description"] == "Test Task 1"
    assert data[0]["priority"] == "High"
    assert data[0]["status"] == "To Do"

def test_update_task():
    # Insert task
    inserted = database.create_tasks([{"description": "Old Desc"}])
    task_id = inserted[0]["id"]
    
    # Update via API
    response = client.put(f"/api/tasks/{task_id}", json={"description": "New Desc", "status": "In Progress"})
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "New Desc"
    assert data["status"] == "In Progress"

def test_delete_task():
    # Insert task
    inserted = database.create_tasks([{"description": "To be deleted"}])
    task_id = inserted[0]["id"]
    
    # Delete via API
    response = client.delete(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    
    # Verify deletion
    response2 = client.get(f"/api/tasks/{task_id}")
    assert response2.status_code == 404

def test_llm_extract_mocked(monkeypatch):
    """Test the extraction endpoint by mocking the LLM call."""
    import llm
    
    def mock_extract(text):
        return [{"description": "Mocked Task", "due_date": "", "owner": "Mock Owner", "priority": "Low", "status": "To Do"}]
    
    monkeypatch.setattr(llm, "extract_tasks_from_text", mock_extract)
    
    response = client.post("/api/tasks/extract", json={"text": "This is some test notes. Over 10 chars."})
    assert response.status_code == 200
    data = response.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["description"] == "Mocked Task"
