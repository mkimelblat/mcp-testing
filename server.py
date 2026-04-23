#!/usr/bin/env python3
"""Launch the Calendly MCP test harness web UI."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1", port=8000,
        reload=True,
    )
