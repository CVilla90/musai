"""Every Gemini call in MUSAI goes through this package.

`gemini.py` owns the SDK call and its hard limits; `budget.py` owns who is allowed to spend
how much per day. Nothing else in the codebase should import `google.genai` directly.
"""
