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