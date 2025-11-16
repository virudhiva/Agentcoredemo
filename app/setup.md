CodeGen Agent – AgentCore POC Setup & Run Guide

1. Project Structure
Projects/
└── AI/
    ├── code_gen_agent.py
    ├── venv/         (Python env for development)
    └── vagent/       (Python env for AgentCore runtime)

2. Activate Environments

Activate development environment:

source venv/Scripts/activate


Activate AgentCore environment:

source vagent/Scripts/activate

3. Configure & Launch the CodeGen Agent

Configure the agent:

agentcore configure -e code_gen_agent.py


Launch the agent runtime:

agentcore launch

Why We Switched from S3 Direct Code Deploy to Docker Container Deploy

Originally, we attempted Direct Code Deploy (S3-based deployment).
However, this approach failed because:

The code_gen agent environment had multiple Python dependencies, especially:

boto3

bedrock_agentcore

strands

fastapi/pydantic imports

utility packages

These dependencies pushed the package size beyond the limit allowed for S3-based direct deployment.

AgentCore fallback logic automatically detected that the code bundle exceeded what S3 Direct Deploy can handle and switched to container-based deployment.

Container deployment solves this because Docker images can hold:

Large dependencies

Native binaries

Compiled wheels

Larger project sizes without restrictions

Therefore, Docker container deployment became the only reliable path for hosting the CodeGen Agent with all its required libraries.

In short:
Direct S3 Deploy fails due to size constraints. Docker deploy succeeds because it supports larger, dependency-heavy codebases.

Error Fixes During Setup
Error 1: Missing uv
Warning: Direct Code Deploy deployment unavailable (uv not found).

Fix:
pip install uv

Error 2: Missing zip Utility
Warning: Direct Code Deploy deployment unavailable (zip utility not found).

Fix:

Install GnuWin32 Zip:

winget install GnuWin32.Zip


Add the binary to PATH:

export PATH="$PATH:/c/PROGRA~2/GnuWin32/bin"

4. Test Payload
{
  "requirement": "Create a simple FastAPI service with one GET /health endpoint.",
  "language": "python"
}

Runtime Initialization Timeout Error
Unable to invoke endpoint successfully:
Runtime initialization time exceeded.


This means the CodeGen Agent is not completing startup within the 60s initialization window.

Typical causes:

Heavy imports or slow initialization logic

Too many dependencies loaded at global scope

Missing or misplaced entrypoint (if __name__ == "__main__": app.run())

Runtime container not optimized

Share your latest code_gen_agent.py if you want me to optimize and fix the startup time.