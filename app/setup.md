# CodeGen Agent – AgentCore POC Setup & Run Guide

## 1. Project Structure

```
Projects/
└── AI/
    ├── code_gen_agent.py
    ├── venv/         (Python env for development)
    └── vagent/       (Python env for AgentCore runtime)
```

## 2. Activate Environments

Activate development environment:
```
source venv/Scripts/activate
```

Activate AgentCore environment:
```
source vagent/Scripts/activate
```

## 3. Configure & Launch the CodeGen Agent

Configure the agent:
```
agentcore configure -e code_gen_agent.py
```

Launch the runtime:
```
agentcore launch
```

## 4. Why We Switched from S3 Direct Code Deploy to Docker Container Deploy

Initially we attempted Direct Code Deploy (S3-based deployment).  
However, this approach failed due to package size limitations.

### Root Cause
The CodeGen Agent uses several heavy Python dependencies, including:

- boto3  
- bedrock_agentcore  
- strands  
- fastapi / pydantic  
- other utilities  

These made the deployment bundle exceed the size allowed for S3 direct deployment.

### AgentCore Behavior
AgentCore automatically detected that the code bundle was too large and fell back to container-based deployment.

### Why Docker Deployment Works
Docker containers overcome these limitations because they:
- support large dependencies  
- can include native binaries  
- can ship compiled wheels  
- allow bigger project sizes  
- offer predictable startup environments  

### Summary
**S3 Direct Deploy fails due to size limits. Docker deploy succeeds because it supports larger, dependency-heavy codebases.**

## 5. Error Fixes During Setup

### Error 1 — Missing `uv`
```
Warning: Direct Code Deploy deployment unavailable (uv not found).
```

Fix:
```
pip install uv
```

### Error 2 — Missing `zip` Utility
```
Warning: Direct Code Deploy deployment unavailable (zip utility not found).
```

Install GnuWin32 Zip:
```
winget install GnuWin32.Zip
```

Add binary to PATH:
```
export PATH="$PATH:/c/PROGRA~2/GnuWin32/bin"
```

## 6. Test Payload

```
{
  "requirement": "Create a simple FastAPI service with one GET /health endpoint.",
  "language": "python"
}
```

## 7. Runtime Initialization Timeout Error

```
Unable to invoke endpoint successfully:
Runtime initialization time exceeded.
```

This means the CodeGen Agent didn’t initialize within the 60s startup window.

### Common Causes
- Heavy imports at global scope  
- Too many dependencies  
- Slow initialization logic  
- Missing or misplaced entrypoint  
  (`if __name__ == "__main__": app.run()`)  
- Non-optimized container base image  

