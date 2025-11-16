## System Workflow (High-Level Execution Flow)

1. The backend receives a request with `projectId`, `language`, and `requirement`, validates it, and prepares it for processing.
2. The system loads the existing project snapshot and files from S3; if none exist, it treats this as a new project.
3. For a new project, the initial prompt is sent to the LLM to generate a fresh multi-file codebase, which is parsed, summarized, and saved.
4. For an existing project, the workflow proceeds in two stages: a planning stage followed by an update stage.
5. In the planning stage, the system uses the planning prompt (`build_plan_prompt`) to get a JSON plan listing files to update, new files to create, and short notes.
6. If the plan response cannot be parsed as valid JSON, a fallback plan is created using keyword relevance against stored file summaries.
7. In the update stage, the system builds an update prompt (`build_update_prompt`) that includes the plan and relevant file contents, and sends it to the LLM.
8. The LLM returns updated or newly generated files in `<<<FILE:...>>>` blocks, which the system parses and merges into the project.
9. The merged files and updated snapshot are saved back to S3 so the project maintains state for future updates.
10. The final output returned to the user contains all generated or updated files in clean, structured `<<<FILE:...>>>` format.
