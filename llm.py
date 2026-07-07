"""
llm.py - Intelligence layer for the AI Project Manager Assistant.

Handles all interactions with the Google Gemini LLM, including:
- Task extraction from unstructured text.
- Project status summarization.

Uses Pydantic schemas to enforce structured JSON output from the model.
"""

import os
import json
import logging
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class TaskExtract(BaseModel):
    """Schema for a single task extracted by the LLM."""
    reasoning: str = Field(description="Brief step-by-step reasoning for extracting this task, inferring its priority, and identifying the owner. Think before extracting.")
    description: str = Field(description="A clear, concise description of the task.")
    due_date: str = Field(
        default="",
        description="The deadline or due date (e.g., '2024-12-31', 'Next Friday'). Empty string if not mentioned.",
    )
    owner: str = Field(
        default="",
        description="The person responsible for the task. Empty string if not mentioned.",
    )
    priority: str = Field(
        default="Medium",
        description="Priority level: 'High', 'Medium', or 'Low'. Infer from context if not explicit.",
    )


class TaskExtractionResponse(BaseModel):
    """Schema for the full LLM extraction response."""
    tasks: list[TaskExtract]


def _get_client() -> genai.Client:
    """
    Create and return a Google GenAI client.

    Raises:
        ValueError: If the GEMINI_API_KEY environment variable is not set.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY is not set in the environment.")
        raise ValueError(
            "GEMINI_API_KEY environment variable is not set. "
            "Please add it to your .env file. See .env.example for reference."
        )
    return genai.Client(api_key=api_key)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
def extract_tasks_from_text(text: str) -> list[dict]:
    """
    Extract structured tasks from unstructured meeting notes or descriptions.

    Uses Gemini with a strict JSON schema to parse free-form text into
    a list of tasks with description, due_date, owner, and priority.
    Includes automatic retry logic via Tenacity for transient API failures.

    Args:
        text: The raw, unstructured text to parse.

    Returns:
        A list of task dictionaries ready for database insertion.

    Raises:
        ValueError: If the API key is not configured.
        RuntimeError: If the LLM call fails after retries.
    """
    client = _get_client()

    prompt = (
        "You are an AI Project Manager Assistant. Your job is to extract actionable "
        "tasks from the following meeting notes or task descriptions.\n\n"
        "For each task you identify:\n"
        "1. Write out your reasoning first.\n"
        "2. Write a clear, concise description.\n"
        "3. Extract the due date if mentioned (use ISO format YYYY-MM-DD when possible).\n"
        "4. Identify the owner/assignee if mentioned.\n"
        "5. Infer the priority (High, Medium, Low) based on urgency cues.\n\n"
        "--- EXAMPLE ---\n"
        "Input: 'We need to fix the login bug immediately, it's crashing production. Sarah, please handle this by EOD.'\n"
        "Output:\n"
        "[\n"
        "  {\n"
        "    \"reasoning\": \"The login bug is crashing production, which indicates critical urgency. Sarah is explicitly assigned. Deadline is EOD.\",\n"
        "    \"description\": \"Fix production crash caused by login bug\",\n"
        "    \"due_date\": \"Today\",\n"
        "    \"owner\": \"Sarah\",\n"
        "    \"priority\": \"High\"\n"
        "  }\n"
        "]\n"
        "---------------\n\n"
        f"Notes:\n---\n{text}\n---"
    )

    logger.info(f"Sending extraction request to LLM ({len(text)} chars of input).")

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TaskExtractionResponse,
                temperature=0.1,
            ),
        )

        data = json.loads(response.text)
        tasks = data.get("tasks", [])
        logger.info(f"LLM extracted {len(tasks)} tasks successfully.")
        return tasks

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        raise RuntimeError(f"LLM returned invalid JSON: {e}")
    except Exception as e:
        logger.warning(f"LLM extraction failed (will retry if allowed): {e}")
        raise RuntimeError(f"Task extraction failed: {e}")


def summarize_project_status(tasks: list[dict]) -> str:
    """
    Generate a natural-language project status summary using the LLM.

    This is a bonus feature that analyzes all current tasks and produces
    a concise executive summary suitable for stakeholder updates.

    Args:
        tasks: A list of all task dictionaries from the database.

    Returns:
        A markdown-formatted summary string.

    Raises:
        ValueError: If the API key is not configured.
        RuntimeError: If the LLM call fails.
    """
    if not tasks:
        return "No tasks in the system yet. Extract some tasks from meeting notes to get started."

    client = _get_client()

    # Build a structured overview for the LLM
    task_lines = []
    for t in tasks:
        task_lines.append(
            f"- [{t.get('status', 'To Do')}] \"{t.get('description', '')}\" "
            f"(Owner: {t.get('owner') or 'Unassigned'}, "
            f"Priority: {t.get('priority', 'Medium')}, "
            f"Due: {t.get('due_date') or 'No deadline'})"
        )
    task_block = "\n".join(task_lines)

    prompt = (
        "You are a senior project manager. Based on the following task list, write a "
        "concise project status summary in markdown format.\n\n"
        "Include:\n"
        "1. A one-line overall status (e.g., 'On Track', 'At Risk', 'Behind Schedule').\n"
        "2. Key highlights (what's done or nearly done).\n"
        "3. Blockers or risks (overdue or high-priority items not yet started).\n"
        "4. A brief recommendation for next steps.\n\n"
        "Keep it under 200 words. Be direct and professional.\n\n"
        f"Tasks:\n{task_block}"
    )

    logger.info("Requesting project status summary from LLM.")

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3),
        )
        summary = response.text
        logger.info("Project summary generated successfully.")
        return summary
    except Exception as e:
        logger.error(f"Failed to generate project summary: {e}")
        raise RuntimeError(f"Summary generation failed: {e}")
