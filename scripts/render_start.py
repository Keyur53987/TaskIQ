"""Portable web entry point: honor Render PORT without shell expansion or proxy trust."""
import os
import sys


def command():
    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    return [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0",
            "--port", str(port), "--workers", "2", "--no-access-log",
            "--no-proxy-headers", "--limit-concurrency", "100", "--timeout-keep-alive", "5"]


if __name__ == "__main__":
    argv = command()
    os.execv(argv[0], argv)
