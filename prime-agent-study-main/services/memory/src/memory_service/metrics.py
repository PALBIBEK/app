"""In-process metrics — four predictive signals from memory.md Phase 5."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class MemoryMetrics:
    recall_injections: int = 0
    recall_items_injected: int = 0
    recall_items_marked_used: int = 0  # set via report_recall_used
    extraction_jobs_done: int = 0
    extraction_items_kept: int = 0
    extraction_jobs_skipped_barren: int = 0
    bank_writes: int = 0
    bank_supersessions: int = 0
    bank_forgets: int = 0
    resume_ok: int = 0
    resume_corrupt: int = 0
    archive_sessions: int = 0
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            precision = (
                self.recall_items_marked_used / self.recall_items_injected
                if self.recall_items_injected
                else 0.0
            )
            yield_per_job = (
                self.extraction_items_kept / self.extraction_jobs_done
                if self.extraction_jobs_done
                else 0.0
            )
            growth_vs_super = (
                self.bank_writes / max(1, self.bank_supersessions)
            )
            resume_rate = (
                self.resume_ok / (self.resume_ok + self.resume_corrupt)
                if (self.resume_ok + self.resume_corrupt)
                else 1.0
            )
            return {
                # Four predictive metrics
                "recall_precision": precision,
                "extraction_yield": yield_per_job,
                "bank_growth_vs_supersession": growth_vs_super,
                "resume_success_rate": resume_rate,
                # Supporting counters
                "recall_injections": self.recall_injections,
                "recall_items_injected": self.recall_items_injected,
                "recall_items_marked_used": self.recall_items_marked_used,
                "extraction_jobs_done": self.extraction_jobs_done,
                "extraction_items_kept": self.extraction_items_kept,
                "extraction_jobs_skipped_barren": self.extraction_jobs_skipped_barren,
                "bank_writes": self.bank_writes,
                "bank_supersessions": self.bank_supersessions,
                "bank_forgets": self.bank_forgets,
                "resume_ok": self.resume_ok,
                "resume_corrupt": self.resume_corrupt,
                "archive_sessions": self.archive_sessions,
                "embed_cache_hits": self.embed_cache_hits,
                "embed_cache_misses": self.embed_cache_misses,
            }

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + n)


METRICS = MemoryMetrics()
