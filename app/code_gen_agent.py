import os
import json
import re
from typing import Dict
from dotenv import load_dotenv

import boto3
from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

REGION = os.getenv("AWS_REGION")

MODEL_ID = os.getenv(
    "MODEL_ID"
)

S3_BUCKET = os.getenv("CODE_GEN_SNAPSHOT_BUCKET")
S3_PREFIX = "projects/"

FILE_MARKER = re.compile(r"^<<<FILE:(.+?)>>>\s*$")


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
    return _project_prefix(project_id) + "files/" + path.lstrip("/")


def load_snapshot(project_id: str) -> Dict:
    """
    Load project snapshot from S3.
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


def save_files(project_id: str, files: Dict[str, str]) -> None:
    s3_client = _s3()
    for path, content in files.items():
        key = _file_key(project_id, path)
        s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=content.encode("utf-8"))


# ----------------------------------------------------------------------
# FILE PARSING / SUMMARIES
# ----------------------------------------------------------------------

def parse_llm_files(text: str) -> Dict[str, str]:
    """
    Parse <<<FILE:...>>> blocks from LLM output.
    Returns dict[path] = content.
    """
    files: Dict[str, str] = {}
    current_path = None
    buffer = []

    for line in text.splitlines():
        m = FILE_MARKER.match(line.strip())
        if m:
            # Save previous file
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


def summarize(content: str, max_lines: int = 10, max_chars: int = 350) -> str:
    lines = [l for l in content.splitlines() if l.strip()]
    snippet = "\n".join(lines[:max_lines])
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "..."
    return snippet


def summarize_all(files: Dict[str, str]) -> Dict[str, str]:
    return {path: summarize(code) for path, code in files.items()}


def relevant_files(prompt: str, summaries: Dict[str, str], max_count: int = 5):
    """
    Very simple keyword-based relevance scoring over summaries.
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
# PROMPT BUILDERS
# ----------------------------------------------------------------------

def build_initial_prompt(project_id: str, language: str, requirement: str) -> str:
    return f"""
You are an expert software engineer.

Create a NEW multi-file project.

Project ID: {project_id}
Primary language: {language}

User requirement:
{requirement}

Rules:
- Generate one or more source files.
- Use <<<FILE:path>>> markers for each file.
- Do NOT include explanations or markdown outside the file blocks.
- For each file, output full file contents, not diffs.
""".strip()


def build_plan_prompt(
    project_id: str,
    language: str,
    requirement: str,
    summaries: Dict[str, str],
) -> str:
    summaries_str = "\n".join(
        f"- {path}: {summary}" for path, summary in summaries.items()
    )
    return f"""
You are analyzing an existing multi-file codebase.

Project ID: {project_id}
Primary language: {language}

User change request:
{requirement}

Existing file summaries:
{summaries_str}

Task:
Return ONLY valid JSON of the form:

{{
  "files_to_update": ["path1", "path2", ...],
  "new_files": ["optional/new/file.ts", ...],
  "notes": "short planning notes"
}}

Do NOT include any code or <<<FILE:...>>> markers.
Do NOT include extra text outside the JSON.
""".strip()


def build_update_prompt(
    project_id: str,
    language: str,
    requirement: str,
    plan: Dict,
    files: Dict[str, str],
) -> str:
    blocks = []
    for path in plan.get("files_to_update", []):
        if path in files:
            blocks.append(f"<<<FILE:{path}>>>\n{files[path]}")
    existing_context = "\n\n".join(blocks) if blocks else "(no relevant files)"

    return f"""
You are updating an existing multi-file project.

Project ID: {project_id}
Primary language: {language}

User change request:
{requirement}

Plan (for your reference):
{json.dumps(plan, indent=2)}

Existing relevant files (full contents):
{existing_context}

Rules:
- Update the necessary files according to the request and plan.
- Create any new files listed in the plan if needed.
- Return ONLY <<<FILE:path>>> blocks with FULL contents of each changed or new file.
- Do NOT include explanations or markdown outside the file blocks.
""".strip()


# ----------------------------------------------------------------------
# AGENT BUILDER (stateless, no memory)
# ----------------------------------------------------------------------

def build_agent() -> Agent:
    system_prompt = (
    "You are an AI software engineer specialized in designing and evolving multi-file "
    "software projects. You generate complete, production-ready codebases.\n\n"
    "OUTPUT FORMAT\n"
    "- Always output code using <<<FILE:relative/path.ext>>> markers.\n"
    "- For every file you mention, output the FULL, final content of that file.\n"
    "- Do not emit any content outside <<<FILE:...>>> blocks.\n"
    "- Do not include Markdown, prose explanations, or placeholder text anywhere.\n\n"
    "CODE QUALITY REQUIREMENTS\n"
    "- Every file must contain only valid, executable code for its language "
    "  (plus minimal inline comments if appropriate).\n"
    "- No pseudo-code, no ellipses (...), no TODOs, and no commented-out stubs "
    "  in place of real implementations.\n"
    "- Ensure the project is runnable after installing dependencies: all imports, "
    "  entrypoints, and configuration must be consistent.\n"
    "- Follow language- and framework-specific best practices for structure, "
    "  naming, error handling, logging, and security.\n\n"
    "SCOPE & SCENARIOS\n"
    "- Implement all core scenarios described by the user, including typical, "
    "  edge, and error paths.\n"
    "- Include appropriate abstractions (layers, modules, interfaces) so the "
    "  project is maintainable and extensible.\n"
    "- Where applicable, include tests in separate test files that are fully "
    "  executable (e.g., unit tests, integration tests).\n\n"
    "STRUCTURE & CONSISTENCY\n"
    "- Keep project structure consistent and coherent across all files.\n"
    "- Respect <<<FILE:...>>> markers precisely (no nesting, no typos in markers).\n"
    "- If you modify an existing project, re-emit the complete, updated contents "
    "  of every affected file.\n"
    "- Do not reference files, functions, or modules that you have not defined.\n\n"
    "GENERAL BEHAVIOR\n"
    "- Prefer clear, idiomatic code over clever or obscure tricks.\n"
    "- Make conservative, sensible assumptions when requirements are ambiguous.\n"
    "- Your entire response must be a set of <<<FILE:...>>> blocks containing only "
    "  fully executable code files for the project."
)

    return Agent(
        model=MODEL_ID,
        system_prompt=system_prompt,  # must be non-empty for Bedrock
        session_manager=None,         # disable any external memory
    )


# ----------------------------------------------------------------------
# MAIN ENTRYPOINT
# ----------------------------------------------------------------------

@app.entrypoint
def invoke(payload, context):
    # Basic debug log to CloudWatch
    try:
        print(json.dumps({"stage": "invoke_start", "payload": payload}))
    except Exception:
        # If payload is not JSON-serializable, ignore logging error
        pass

    project_id = payload.get("projectId", "default-project")
    language = (payload.get("language") or "python").lower()
    requirement = payload.get("prompt") or payload.get("requirement")

    if not requirement:
        return {"error": "Missing 'prompt' or 'requirement' in payload"}

    # Load existing snapshot (if any)
    snapshot = load_snapshot(project_id)
    files: Dict[str, str] = snapshot.get("files", {})
    summaries: Dict[str, str] = snapshot.get("summaries", {})

    # ------------------------------------------------------------------
    # NEW PROJECT FLOW
    # ------------------------------------------------------------------
    if not files:
        agent = build_agent()
        prompt = build_initial_prompt(project_id, language, requirement)
        result = agent(prompt)

        content = result.message.get("content", [])
        if isinstance(content, list) and content:
            raw_text = content[0].get("text", "") or ""
        else:
            raw_text = str(result)

        new_files = parse_llm_files(raw_text)
        if not new_files:
            return {
                "error": "Model did not return any <<<FILE:...>>> blocks",
                "rawResponse": raw_text,
            }

        summaries = summarize_all(new_files)

        new_snapshot = {
            "projectId": project_id,
            "language": language,
            "files": new_files,
            "summaries": summaries,
        }

        save_snapshot(project_id, new_snapshot)
        save_files(project_id, new_files)

        response_text = "\n\n".join(
            f"<<<FILE:{path}>>>\n{code}" for path, code in new_files.items()
        )

        return {
            "projectId": project_id,
            "created": True,
            "response": response_text,
        }

    # ------------------------------------------------------------------
    # EXISTING PROJECT FLOW (PLAN → UPDATE)
    # ------------------------------------------------------------------

    # PLAN
    agent = build_agent()
    plan_prompt = build_plan_prompt(project_id, language, requirement, summaries)
    plan_result = agent(plan_prompt)

    content = plan_result.message.get("content", [])
    if isinstance(content, list) and content:
        plan_text = content[0].get("text", "") or ""
    else:
        plan_text = str(plan_result)

    try:
        plan = json.loads(plan_text)
    except Exception:
        # Fallback: pick relevant files by summaries only
        plan = {
            "files_to_update": relevant_files(requirement, summaries),
            "new_files": [],
            "notes": "fallback-plan-json-parse-failed",
            "rawPlanText": plan_text,
        }

    # UPDATE
    agent = build_agent()
    update_prompt = build_update_prompt(
        project_id, language, requirement, plan, files
    )
    update_result = agent(update_prompt)

    content = update_result.message.get("content", [])
    if isinstance(content, list) and content:
        update_text = content[0].get("text", "") or ""
    else:
        update_text = str(update_result)

    updated_files = parse_llm_files(update_text)
    if not updated_files:
        return {
            "projectId": project_id,
            "error": "Model did not return any updated files",
            "plan": plan,
            "rawResponse": update_text,
        }

    # Merge updated files into snapshot
    files.update(updated_files)
    summaries = summarize_all(files)

    new_snapshot = {
        "projectId": project_id,
        "language": language,
        "files": files,
        "summaries": summaries,
    }

    save_snapshot(project_id, new_snapshot)
    save_files(project_id, files)

    response_text = "\n\n".join(
        f"<<<FILE:{path}>>>\n{code}" for path, code in files.items()
    )

    return {
        "projectId": project_id,
        "created": False,
        "plan": plan,
        "response": response_text,
    }

if __name__ == "__main__":
    app.run()