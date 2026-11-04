"""Exercise the installed SDK's real schema conversion without a paid network call."""
import json
from google import genai
from google.genai import types
import llm


def test_meeting_schema_serializes_through_google_sdk(monkeypatch):
    client = genai.Client(api_key="test-only-not-a-real-key")
    captured = []
    def fake_request(method, path, request_dict, *args, **kwargs):
        captured.append(request_dict)
        output = {"tasks": [{"description": "Update guide", "owner": "Aman", "due_date": "Friday", "priority": "Medium", "source_quote": "Aman will update the guide by Friday."}]}
        return types.HttpResponse(headers={}, body=json.dumps({"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(output)}]}, "finishReason": "STOP"}]}))
    monkeypatch.setattr(client._api_client, "request", fake_request)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    result = llm.extract_meeting_proposals.__wrapped__("Aman will update the guide by Friday.", "2026-11-04", "Asia/Kolkata")
    assert result[0]["source_quote"] == "Aman will update the guide by Friday."
    assert captured[0]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured[0]["generationConfig"]["responseSchema"]["properties"]["tasks"]["items"]["properties"]["source_quote"]
