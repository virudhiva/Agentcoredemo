1. User submits a **requirement** (new project) or **change request** (existing project) via **AgentCore**.
2. Requirement is **chunked** and **summarized**; summaries are merged into a single **Global Project Spec**.
3. The model generates a structured **multi-file plan** (file **paths** + **roles**) based on **language/framework**.
4. For **new projects**, each file is **individually generated** using the global spec and saved to **S3**.
5. A **snapshot** (files, roles, summaries, globalSpec) is stored for **incremental updates**.
6. For updates, the request is converted into a **Change Spec JSON** describing **impacted** + **new files**.
7. **Impacted files** are identified using the change spec (or **fallback relevance scoring**).
8. Only the impacted files are **regenerated** using **old code + change spec + global spec**.
9. Any **new files** required by the change spec are generated and appended to the **snapshot**.
10. The response returns only the **changed/new files** using **<<<FILE:path>>>** blocks.
