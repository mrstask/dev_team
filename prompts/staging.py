STAGING_INSTRUCTION = """
CRITICAL — FILE WRITING RULES:
- You may READ files from anywhere in the project using their normal paths.
- You must WRITE all output files into the staging directory: dev_team/_staging/
- Preserve the full relative path inside staging.
  Example: to produce backend/app/models/article.py
           write to:  dev_team/_staging/backend/app/models/article.py
- Do NOT write to any real project path — staging only.
- Write every file completely (no truncation, no placeholders inside the file).
"""
