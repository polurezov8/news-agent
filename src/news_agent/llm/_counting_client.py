"""Thin wrapper around ChatAnthropic that records token usage to SQLite after each invoke."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from news_agent.llm.costs import usd_cents
from news_agent.storage.repository import connect, save_llm_cost


class CountingClient:
    """Implements .invoke(...) like ChatAnthropic; logs cost after each call.

    Drop-in replacement for the bare ChatAnthropic client used by tagger/scorer/summarizer.
    """

    def __init__(
        self,
        base,                      # ChatAnthropic instance
        *,
        model: str,
        purpose: str,              # "tag" | "score" | "summarize"
        db_path: Path,
    ) -> None:
        self._base = base
        self._model = model
        self._purpose = purpose
        self._db_path = db_path

    def invoke(self, messages, *args, **kwargs):
        response = self._base.invoke(messages, *args, **kwargs)
        usage = getattr(response, "usage_metadata", None) or {}
        in_t = int(usage.get("input_tokens") or 0)
        out_t = int(usage.get("output_tokens") or 0)
        if in_t or out_t:
            cents = usd_cents(self._model, in_t, out_t)
            conn = connect(self._db_path)
            try:
                save_llm_cost(
                    conn,
                    model=self._model,
                    input_tokens=in_t,
                    output_tokens=out_t,
                    usd_cents=cents,
                    purpose=self._purpose,
                    article_id=None,
                    at=datetime.now(timezone.utc),
                )
                conn.commit()
            finally:
                conn.close()
        return response
