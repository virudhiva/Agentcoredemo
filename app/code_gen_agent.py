import os
import json
import re
from typing import Dict, List, Any

import boto3
from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

REGION = os.getenv("AWS_REGION", "us-west-2")

MODEL_ID = os.getenv(
    "MODEL_ID"
)

# Code gen snapshot bucket (project metadata + summaries)
S3_BUCKET = os.getenv("CODE_GEN_SNAPSHOT_BUCKET")
S3_PREFIX = "projects/"

# Files will be stored under this prefix as raw code
CODE_PREFIX = "files/"
REQ_PREFIX = "requirements/"

FILE_MARKER = re.compile(r"^<<<FILE:(.+?)>>>\s*$")  # kept for compatibility if needed


# ----------------------------------------------------------------------
# S3 HELPERS
# ----------------------------------------------------------------------

def _s3():
    return boto3.client("s3", region_name=REGION)


def _project_prefix(project_id: str) -> str:
    return f"{S3_PREFIX.rstrip('/')}/{project_id}/"


def _snapshot_key(project_id: str) -> str:
    return _project_prefix(project_id) + "snapshot.json"


def _file_key(project_id: str, path: str) -> str:
    return _project_prefix(project_id) + CODE_PREFIX + path.lstrip("/")


def _requirement_key(project_id: str, requirement_id: str) -> str:
    return _project_prefix(project_id) + REQ_PREFIX + f"{requirement_id}.txt"


def load_snapshot(project_id: str) -> Dict:
    """
    Load project snapshot from S3.
    Snapshot structure (new architecture):

    {
      "projectId": "...",
      "language": "...",
      "framework": "...",
      "globalSpec": "<stringified JSON spec>",
      "files": ["path1", "path2", ...],
      "roles": { "path": "role description", ... },
      "summaries": { "path": "short summary", ... }
    }

    Returns {} if not found.
    """
    key = _snapshot_key(project_id)
    try:
        obj = _s3().get_object(Bucket=S3_BUCKET, Key=key)
        data = obj["Body"].read()
        snapshot = json.loads(data)
        return snapshot
    except Exception:
        # New project (no snapshot yet)
        return {}


def save_snapshot(project_id: str, snapshot: Dict) -> None:
    key = _snapshot_key(project_id)
    body = json.dumps(snapshot, indent=2).encode("utf-8")
    _s3().put_object(Bucket=S3_BUCKET, Key=key, Body=body)


def save_code_files(project_id: str, files: Dict[str, str]) -> None:
    """
    Store full code files in S3. Code is not stored in snapshot.
    """
    s3_client = _s3()
    for path, content in files.items():
        key = _file_key(project_id, path)
        s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=content.encode("utf-8"))


def load_code_file(project_id: str, path: str) -> str:
    key = _file_key(project_id, path)
    obj = _s3().get_object(Bucket=S3_BUCKET, Key=key)
    return obj["Body"].read().decode("utf-8")


def save_requirement(project_id: str, requirement_text: str) -> str:
    req_id = os.urandom(8).hex()
    key = _requirement_key(project_id, req_id)
    _s3().put_object(Bucket=S3_BUCKET, Key=key, Body=requirement_text.encode("utf-8"))
    return key


# ----------------------------------------------------------------------
# UTILITIES
# ----------------------------------------------------------------------

def parse_llm_files(text: str) -> Dict[str, str]:
    """
    Parse <<<FILE:...>>> blocks from LLM output.
    Kept for compatibility; not used in the new per-file generation flow,
    but may be useful for debugging or future extensions.
    """
    files: Dict[str, str] = {}
    current_path = None
    buffer: List[str] = []

    for line in text.splitlines():
        m = FILE_MARKER.match(line.strip())
        if m:
            if current_path is not None:
                files[current_path] = "\n".join(buffer).rstrip() + "\n"
            current_path = m.group(1).strip()
            buffer = []
        else:
            if current_path is not None:
                buffer.append(line)

    if current_path is not None:
        files[current_path] = "\n".join(buffer).rstrip() + "\n"

    return files


def summarize_snippet(content: str, max_lines: int = 10, max_chars: int = 350) -> str:
    lines = [l for l in content.splitlines() if l.strip()]
    snippet = "\n".join(lines[:max_lines])
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "..."
    return snippet


def relevant_files(prompt: str, summaries: Dict[str, str], max_count: int = 5):
    """
    Very simple keyword-based relevance scoring over summaries.
    Used as a fallback if LLM planning/impact analysis fails.
    """
    words = [
        w.lower()
        for w in re.findall(r"[a-zA-Z0-9_]+", prompt)
        if len(w) > 2
    ]

    scored = []
    for path, summ in summaries.items():
        text = (path + " " + summ).lower()
        score = sum(text.count(w) for w in words)
        scored.append((path, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    hits = [p for p, s in scored if s > 0][:max_count]
    if not hits:
        hits = [p for p, _ in scored[:2]]

    return hits


# ----------------------------------------------------------------------
# MODEL CALL HELPER (Amazon Nova via strands.Agent)
# ----------------------------------------------------------------------

def call_model(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str:
    """
    Stateless LLM call using strands.Agent with Nova.
    strands.Agent accepts only (model, system_prompt, session_manager).
    Generation settings must be passed to the call, not the constructor.
    """
    agent = Agent(
        model=MODEL_ID,
        system_prompt=system_prompt or "You are a helpful assistant.",
        session_manager=None    # stateless, no memory
    )

    # Generation parameters must be passed here
    result = agent(
        user_prompt,
        max_tokens=max_tokens,
        temperature=0.2
    )

    # Extract text content safely
    content = result.message.get("content", [])
    if isinstance(content, list) and content:
        return content[0].get("text", "") or ""
    return str(result)




# ----------------------------------------------------------------------
# CASE 1: NEW PROJECT FLOW (Chunk → Summaries → Global Spec → Plan → Per-file Code)
# ----------------------------------------------------------------------

def chunk_requirement(text: str, max_chars: int = 6000) -> List[str]:
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        chunks.append(text[start:end])
        start = end
    return chunks


def summarize_requirement_chunk(chunk: str, idx: int) -> str:
    system = "You summarize large software requirements into structured JSON."
    user = f"""
You are summarizing part of a large requirement.

Chunk {idx+1}:

{chunk}

Return ONLY JSON with fields:
- modules
- entities
- endpoints
- rules
"""
    return call_model(system, user, max_tokens=2000)


def build_global_spec_from_chunks(chunk_summaries: List[str]) -> str:
    """
    Combine multiple JSON fragments into a single global project spec JSON.
    We let the model do the merge.
    """
    system = (
        "You merge partial JSON requirement summaries into a single global project "
        "specification JSON. Preserve all modules, entities, endpoints, and rules."
    )
    user = "Merge the following JSON fragments into one JSON:\n\n" + "\n\n".join(
        chunk_summaries
    )
    return call_model(system, user, max_tokens=8000)


def plan_files_from_global_spec(global_spec: str, language: str, framework: str) -> List[Dict[str, Any]]:
    """
    Ask LLM to propose a per-file plan (path + role), but enforce correct stack.
    """
    system = (
        "You are an expert software architect designing multi-file project structures."
    )

    user = f"""
You MUST generate a file structure ONLY for:

Language: {language}
Framework: {framework}

STRICT RULES:
- If framework is nestjs → use src/main.ts, modules/, controllers/, services/, dto/
- DO NOT generate Python or Flask. EVER.
- DO NOT generate .py files.
- DO NOT generate other frameworks.
- Use valid TypeScript file paths.

Global specification:
{global_spec}

Return ONLY JSON list in format:
[
  {{ "path": "src/....ts", "role": "..." }},
  ...
]
"""

    text = call_model(system, user, max_tokens=4000)

    try:
        plan = json.loads(text)
        assert isinstance(plan, list)
        return plan
    except:
        if language == "typescript" and framework == "nestjs":
            return [
                {"path": "src/main.ts", "role": "bootstrap module"},
                {"path": "src/app.module.ts", "role": "root module"},
                {"path": "src/app.controller.ts", "role": "controller"},
                {"path": "src/app.service.ts", "role": "service"},
            ]
        return []



def generate_file_code(global_spec: str, file_meta: Dict[str, Any]) -> str:
    """
    Generate code for a single file with guaranteed NestJS + TypeScript.
    """
    path = file_meta["path"]
    role = file_meta.get("role", "")

    system = (
        "You are an expert software engineer generating a FULL source file."
    )

    user = f"""
Generate the FULL code for the file.

Path: {path}
Role: {role}

STRICT RULES:
- MUST be TypeScript, valid syntax.
- MUST use NestJS framework conventions.
- NEVER generate Python.
- NEVER generate Flask.
- NEVER generate non-TS code.
- MUST use NestJS decorators: @Module, @Controller, @Injectable, etc.
- MUST return ONLY:

<<<FILE:{path}>>>
<full TypeScript NestJS code>

Global project spec:
{global_spec}
"""

    return call_model(system, user, max_tokens=8000)



def summarize_file_code(path: str, role: str, code: str) -> str:
    system = "You summarize code files in one concise paragraph."
    user = f"""
File path: {path}
File role: {role}

Code:
{code}
"""
    return call_model(system, user, max_tokens=512)


def handle_create_project(project_id: str, language: str, framework: str, requirement: str):
    """
    Implements Case 1 with the new architecture for a NEW project:
      - chunk requirement
      - summarize each chunk
      - merge to global spec
      - plan files
      - generate each file
      - save code to S3
      - save snapshot with summaries and roles
    """
    # 1) store raw requirement snapshot
    req_key = save_requirement(project_id, requirement)

    # 2) chunk requirement
    chunks = chunk_requirement(requirement, max_chars=6000)

    # 3) summarize each chunk
    chunk_summaries: List[str] = []
    for idx, c in enumerate(chunks):
        chunk_summaries.append(summarize_requirement_chunk(c, idx))

    # 4) build global spec
    global_spec = build_global_spec_from_chunks(chunk_summaries)

    # 5) plan files
    file_plan = plan_files_from_global_spec(global_spec, language, framework)

    # 6) generate code per file
    generated_files: Dict[str, str] = {}
    roles: Dict[str, str] = {}
    summaries: Dict[str, str] = {}

    for meta in file_plan:
        path = meta["path"]
        role = meta.get("role", "")
        code = generate_file_code(global_spec, meta)
        generated_files[path] = code
        roles[path] = role
        summaries[path] = summarize_file_code(path, role, code)

    # 7) save code files
    save_code_files(project_id, generated_files)

    # 8) snapshot
    snapshot = {
        "projectId": project_id,
        "language": language,
        "framework": framework,
        "globalSpec": global_spec,
        "files": list(generated_files.keys()),
        "roles": roles,
        "summaries": summaries,
        "lastRequirementKey": req_key,
    }
    save_snapshot(project_id, snapshot)

    # 9) Build response (for debugging / client consumption)
    response_text = "\n\n".join(
        f"<<<FILE:{path}>>>\n{code}" for path, code in generated_files.items()
    )

    return {
        "projectId": project_id,
        "created": True,
        "fileCount": len(generated_files),
        "requirementKey": req_key,
        "response": response_text,
    }


# ----------------------------------------------------------------------
# CASE 2: EXISTING PROJECT FLOW (Change Spec → Impacted Files → Per-file Update)
# ----------------------------------------------------------------------

def build_change_spec(change_request: str, snapshot: Dict[str, Any]) -> str:
    """
    Convert natural language change request into structured change spec JSON.
    """
    files = snapshot.get("files", [])
    roles = snapshot.get("roles", {})
    summaries = snapshot.get("summaries", {})

    file_meta = []
    for p in files:
        file_meta.append(
            {
                "path": p,
                "role": roles.get(p, ""),
                "summary": summaries.get(p, ""),
            }
        )

    system = (
        "You are a senior engineer. Convert change requests into structured JSON specs "
        "for updating an existing multi-file project."
    )
    user = f"""
Change request from user:
{change_request}

Existing files (path, role, summary):
{json.dumps(file_meta, indent=2)}

Return ONLY JSON like:
{{
  "changeType": "...",
  "impactedFiles": ["path1", "path2", ...],
  "newFiles": ["optional/new/file.py", ...],
  "notes": "short planning notes"
}}
"""
    return call_model(system, user, max_tokens=3000)


def find_impacted_files_from_spec(change_spec_text: str, snapshot: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    From the change spec JSON, extract impactedFiles and newFiles.
    If parse fails or fields missing, use a fallback strategy.
    """
    files = snapshot.get("files", [])
    summaries = snapshot.get("summaries", {})

    impacted = []
    new_files = []

    try:
        spec_obj = json.loads(change_spec_text)
        if isinstance(spec_obj, dict):
            impacted = spec_obj.get("impactedFiles", []) or spec_obj.get("files_to_update", [])
            new_files = spec_obj.get("newFiles", [])
    except Exception:
        pass

    # Fallback if no impacted files found
    if not impacted:
        requirement = change_spec_text  # best we can do in fallback
        impacted = relevant_files(requirement, summaries)

    # Ensure impacted files are valid paths within the project
    impacted = [p for p in impacted if p in files]

    return {
        "impactedFiles": impacted,
        "newFiles": new_files,
    }


def regenerate_file_from_change(
    project_id: str,
    path: str,
    old_code: str,
    change_spec_text: str,
    global_spec: str,
    role: str,
) -> str:
    """
    Regenerate a single file based on TypeScript/NestJS rules.
    """
    system = (
        "You are updating an existing TypeScript NestJS code file."
    )

    user = f"""
You MUST update this file strictly using:

Language: TypeScript
Framework: NestJS

NEVER generate Python.
NEVER generate Flask.
NEVER output other frameworks.

File path: {path}
Role: {role}

Old Code:
{old_code}

Change Spec (JSON):
{change_spec_text}

Update this file using correct NestJS patterns.
Return ONLY:

<<<FILE:{path}>>>
<updated TypeScript code>
"""

    return call_model(system, user, max_tokens=8000)



def generate_new_file_from_change(
    project_id: str,
    path: str,
    change_spec_text: str,
    global_spec: str,
) -> str:
    """
    Generate a brand new file needed for the change.
    """
    system = (
        "You are adding a new file to an existing multi-file project. "
        "Return ONLY valid executable code, no explanations."
    )
    user = f"""
Project global spec (JSON):
{global_spec}

New file to create: {path}

Change spec JSON:
{change_spec_text}

Generate the FULL code for this new file.
Return ONLY the code.
"""
    return call_model(system, user, max_tokens=8000)


def handle_update_project(project_id: str, language: str, change_request: str, snapshot: Dict[str, Any]):
    """
    Implements Case 2:
      - build change spec JSON
      - identify impacted existing files (+ optional new files)
      - regenerate impacted files one-by-one
      - generate new files if requested
      - update summaries + snapshot
    """
    global_spec = snapshot.get("globalSpec", "")
    roles = snapshot.get("roles", {})
    summaries = snapshot.get("summaries", {})
    files = snapshot.get("files", [])

    if not files:
        return {
            "projectId": project_id,
            "created": False,
            "error": "No existing files found in snapshot",
        }

    # 1) Build change spec JSON
    change_spec_text = build_change_spec(change_request, snapshot)

    # 2) Determine impacted + new files
    impact_info = find_impacted_files_from_spec(change_spec_text, snapshot)
    impacted_files = impact_info.get("impactedFiles", [])
    new_files = impact_info.get("newFiles", [])

    updated_files: Dict[str, str] = {}
    created_files: Dict[str, str] = {}

    # 3) Regenerate impacted existing files
    for path in impacted_files:
        try:
            old_code = load_code_file(project_id, path)
        except Exception:
            # If file missing in S3, skip or treat as new
            continue

        new_code = regenerate_file_from_change(
            project_id,
            path,
            old_code,
            change_spec_text,
            global_spec,
            roles.get(path, ""),
        )
        updated_files[path] = new_code
        summaries[path] = summarize_file_code(path, roles.get(path, ""), new_code)

    # 4) Generate new files (if any)
    for path in new_files:
        if path in files:
            continue
        code = generate_new_file_from_change(
            project_id,
            path,
            change_spec_text,
            global_spec,
        )
        created_files[path] = code
        roles[path] = "generated for change request"
        summaries[path] = summarize_file_code(path, roles[path], code)
        files.append(path)

    # 5) Persist updated and new files to S3
    if updated_files:
        save_code_files(project_id, updated_files)
    if created_files:
        save_code_files(project_id, created_files)

    # 6) Save updated snapshot
    new_snapshot = {
        "projectId": project_id,
        "language": language,
        "framework": snapshot.get("framework", ""),
        "globalSpec": global_spec,
        "files": files,
        "roles": roles,
        "summaries": summaries,
        "lastChangeSpec": change_spec_text,
    }
    save_snapshot(project_id, new_snapshot)

    # 7) Build response text (only changed + new files)
    all_changed: Dict[str, str] = {}
    all_changed.update(updated_files)
    all_changed.update(created_files)

    if all_changed:
        response_text = "\n\n".join(
            f"<<<FILE:{path}>>>\n{code}" for path, code in all_changed.items()
        )
    else:
        response_text = ""

    return {
        "projectId": project_id,
        "created": False,
        "updatedFiles": list(updated_files.keys()),
        "newFiles": list(created_files.keys()),
        "response": response_text,
        "impactInfo": impact_info,
    }


# ----------------------------------------------------------------------
# MAIN ENTRYPOINT (AgentCore Runtime)
# ----------------------------------------------------------------------

@app.entrypoint
def invoke(payload, context):
    """
    Main entrypoint for AgentCore runtime.
    Decides between:
      - NEW PROJECT (Case 1)
      - EXISTING PROJECT UPDATE (Case 2)
    based on presence of a snapshot and file list.
    """
    try:
        print(json.dumps({"stage": "invoke_start", "payload": payload}))
    except Exception:
        pass

    project_id = payload.get("projectId", "default-project")
    language = (payload.get("language") or "python").lower()
    framework = payload.get("framework") or "fastapi"
    requirement = payload.get("prompt") or payload.get("requirement") or payload.get("changeRequest")

    if not requirement:
        return {"error": "Missing 'prompt', 'requirement' or 'changeRequest' in payload"}

    snapshot = load_snapshot(project_id)
    files = snapshot.get("files", [])

    # NEW PROJECT FLOW
    if not snapshot or not files:
        return handle_create_project(project_id, language, framework, requirement)

    # EXISTING PROJECT FLOW
    return handle_update_project(project_id, language, requirement, snapshot)


if __name__ == "__main__":
    # For local testing only. AgentCore will not use this.
    app.run()
