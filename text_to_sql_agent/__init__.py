"""Text-to-SQL AI Data Analyst Agent.

A LangGraph agent that answers plain-English questions about a relational
database by generating, validating and (if needed) self-healing SQL, then
returning the results with a chart spec and a plain-English insight.

Everything runs on CPU. The LLM is OpenAI-compatible and is configured
ONLY through environment variables -- no key ever lives in this code.
"""

__version__ = "0.1.0"

from .pipeline import PipelineResult, run_pipeline

__all__ = ["__version__", "PipelineResult", "run_pipeline"]
