# Windows 정책을 변경하지 않고 로컬 LangGraph 개발 서버를 숨김 창으로 실행한다.

import argparse
import os
import socket
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=2024)
    port = parser.parse_args().port
    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")
    root = Path(__file__).resolve().parents[1]
    launcher = root / ".venv" / ("Scripts/langgraph.exe" if os.name == "nt" else "bin/langgraph")
    if not launcher.is_file():
        raise SystemExit("Run uv sync first to install development dependencies.")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            print(f"Port {port} is already in use. No process was stopped.")
            print(f"Viewer: http://127.0.0.1:{port}/dev")
            return
    except OSError:
        pass
    logs = root / ".local" / "langgraph"
    logs.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }
    with (
        (logs / "server.log").open("wb") as stdout,
        (logs / "server-error.log").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            [
                str(launcher),
                "dev",
                "--no-browser",
                "--no-reload",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    (logs / "server.pid").write_text(str(process.pid), encoding="utf-8")
    print(f"LangGraph launcher PID: {process.pid}")
    print(f"Viewer: http://127.0.0.1:{port}/dev")
    print(f"Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:{port}")
    print("Startup takes several seconds. Logs: .local/langgraph/server*.log")


if __name__ == "__main__":
    main()
