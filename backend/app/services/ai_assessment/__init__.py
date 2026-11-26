"""Demo narrative: semantic retrieval suggests lines, a configured model writes the reading, guardrails check it.

Off unless TRACE_AI_ASSESSMENT=1 and TRACE_LLM_API_KEY is set. The key is never logged. Candidate text is sent to the
configured OpenAI-compatible provider only on that path. Embeddings stay local (gte-small).
"""
