"""Step-level logging.

Every decision and tool call the pipeline makes is appended as one JSON
object per line (JSONL) to the configured log file, and kept in memory
on the run so the API/UI can show the trace. This is the audit trail a
recruiter or interviewer can inspect: it shows *why* the agent did what
it did, not just the final answer.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field


@dataclass
class StepLog:
    """In-memory trace of one pipeline run, optionally mirrored to disk."""

    log_file: str | None = None
    steps: list[dict] = field(default_factory=list)

    def record(self, step: str, detail: str = "", **extra) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "step": step,
            "detail": detail,
            **extra,
        }
        self.steps.append(entry)
        if self.log_file:
            try:
                os.makedirs(os.path.dirname(self.log_file) or ".", exist_ok=True)
                with open(self.log_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry) + "\n")
            except OSError:
                # Logging must never break the pipeline.
                pass

    def as_dicts(self) -> list[dict]:
        return list(self.steps)
