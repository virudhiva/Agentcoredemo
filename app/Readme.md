# 📘 Code Gen — High-Level Working Flow

Below is the simplified working flow of the **Code Gen Intelligent Code Generator**.

---

## ⭐ Workflow Summary

1. 📝 User submits a **requirement** (new project) or **change request** (update).
2. ✂️ The input is **chunked** and 🔎 **summarized** into a unified **Global Project Spec**.
3. 🗂️ The model produces a **multi-file plan** with file **paths** and **roles**.
4. 🏗️ For **new projects**, each file is generated individually and saved in **S3**.
5. 🗄️ A **snapshot** is created containing globalSpec, file list, roles, and summaries.
6. 🔧 For updates, input is converted into a **Change Spec JSON**.
7. 🎯 Code Gen identifies **impacted files** using structured spec or fallback relevance.
8. 🔄 Only those impacted files are **regenerated** with updated logic.
9. ➕ Any additional **new files** required by the change are created and added.
10. 📤 Final output returns only the **changed / new** files in `<<<FILE:path>>>` format.

### 🚀 Key Problems This Architecture Solves

- 🧱 **Bypasses token-limit failures** through requirement chunking and incremental processing.  
- ✂️ **Prevents output truncation** by generating each file independently rather than as one massive response.  
- 🎯 **Improves change accuracy** with file-level impact analysis instead of regenerating the entire project blindly.  
- 🏗️ **Maintains architectural quality** using global specs, file roles, and code summaries to keep structure consistent.  
- 🔒 **Avoids context loss** by storing project state in S3 snapshots instead of pushing huge prompts or depending on memory.  
