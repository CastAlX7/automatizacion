"""8.8 FinOps — Rastreo de tokens y costos por agente/sesión.

Callback de LangChain que registra tokens consumidos, costo estimado y
latencia por cada llamada al LLM. Se puede usar de forma independiente
o integrado con el monitor de alertas.

Uso standalone:
    from monitoring.cost_tracker import CostTracker
    tracker = CostTracker()
    tracker.record(agent="acquisition", tokens_in=800, tokens_out=200, latency_s=1.2)
    tracker.summary()

Los registros se persisten en monitoring/data/cost_log.jsonl (append-only).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

GROQ_PRICING = {
    "llama-3.3-70b-versatile": {"input_per_1m": 0.59, "output_per_1m": 0.79},
    "llama-3.1-8b-instant": {"input_per_1m": 0.05, "output_per_1m": 0.08},
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "input_per_1m": 0.11,
        "output_per_1m": 0.34,
    },
}

DEFAULT_PRICING = {"input_per_1m": 0.59, "output_per_1m": 0.79}


class CostTracker:
    def __init__(self, log_dir: Path | None = None) -> None:
        self._log_dir = log_dir or DATA_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / "cost_log.jsonl"
        self._lock = threading.Lock()

        self._session_totals: dict[str, float] = {
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0,
            "calls": 0,
            "latency_total_s": 0,
        }
        self._by_agent: dict[str, dict[str, float]] = {}

    def estimate_cost(
        self, tokens_in: int, tokens_out: int, model: str | None = None
    ) -> float:
        model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        pricing = GROQ_PRICING.get(model, DEFAULT_PRICING)
        cost = (tokens_in / 1_000_000) * pricing["input_per_1m"] + (
            tokens_out / 1_000_000
        ) * pricing["output_per_1m"]
        return round(cost, 6)

    def record(
        self,
        agent: str,
        tokens_in: int,
        tokens_out: int,
        latency_s: float,
        model: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cost = self.estimate_cost(tokens_in, tokens_out, model)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "model": model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "latency_s": round(latency_s, 3),
            "session_id": session_id,
        }
        if metadata:
            entry["metadata"] = metadata

        with self._lock:
            self._session_totals["tokens_in"] += tokens_in
            self._session_totals["tokens_out"] += tokens_out
            self._session_totals["cost_usd"] += cost
            self._session_totals["calls"] += 1
            self._session_totals["latency_total_s"] += latency_s

            agent_totals = self._by_agent.setdefault(
                agent,
                {
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_usd": 0,
                    "calls": 0,
                    "latencies": [],
                },
            )
            agent_totals["tokens_in"] += tokens_in
            agent_totals["tokens_out"] += tokens_out
            agent_totals["cost_usd"] += cost
            agent_totals["calls"] += 1
            agent_totals["latencies"].append(latency_s)

            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return entry

    def session_cost(self) -> float:
        return round(self._session_totals["cost_usd"], 6)

    def session_tokens(self) -> int:
        return int(
            self._session_totals["tokens_in"] + self._session_totals["tokens_out"]
        )

    def latency_percentile(self, agent: str, p: int = 95) -> float | None:
        data = self._by_agent.get(agent, {}).get("latencies", [])
        if not data:
            return None
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        return round(sorted_data[min(idx, len(sorted_data) - 1)], 3)

    def summary(self) -> dict[str, Any]:
        all_latencies: list[float] = []
        agent_summary = {}
        for agent, data in self._by_agent.items():
            latencies = data.get("latencies", [])
            all_latencies.extend(latencies)
            sorted_lat = sorted(latencies)
            p50_idx = int(len(sorted_lat) * 0.5)
            p95_idx = int(len(sorted_lat) * 0.95)
            p99_idx = int(len(sorted_lat) * 0.99)
            agent_summary[agent] = {
                "calls": data["calls"],
                "tokens_in": int(data["tokens_in"]),
                "tokens_out": int(data["tokens_out"]),
                "cost_usd": round(data["cost_usd"], 6),
                "latency_p50_s": sorted_lat[min(p50_idx, len(sorted_lat) - 1)]
                if sorted_lat
                else 0,
                "latency_p95_s": sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
                if sorted_lat
                else 0,
                "latency_p99_s": sorted_lat[min(p99_idx, len(sorted_lat) - 1)]
                if sorted_lat
                else 0,
            }

        sorted_all = sorted(all_latencies) if all_latencies else []
        return {
            "total_calls": int(self._session_totals["calls"]),
            "total_tokens": self.session_tokens(),
            "total_cost_usd": round(self._session_totals["cost_usd"], 6),
            "latency_p50_s": sorted_all[int(len(sorted_all) * 0.5)]
            if sorted_all
            else 0,
            "latency_p95_s": sorted_all[int(len(sorted_all) * 0.95)]
            if sorted_all
            else 0,
            "latency_p99_s": sorted_all[int(len(sorted_all) * 0.99)]
            if sorted_all
            else 0,
            "by_agent": agent_summary,
        }

    def load_history(self, since_hours: int = 24) -> list[dict]:
        if not self._log_path.exists():
            return []
        cutoff = time.time() - (since_hours * 3600)
        entries = []
        with open(self._log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                    if ts >= cutoff:
                        entries.append(entry)
                except (json.JSONDecodeError, KeyError):
                    continue
        return entries

    def cost_since(self, hours: int = 24) -> float:
        return round(sum(e.get("cost_usd", 0) for e in self.load_history(hours)), 6)


_tracker: CostTracker | None = None


def get_tracker() -> CostTracker:
    """Instancia compartida por proceso — todos los agentes escriben al mismo log."""
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker


async def track_llm_call(
    agent: str,
    llm: Any,
    messages: Any,
    model: str,
    session_id: str | None = None,
):
    """Invoca un chat model de LangChain registrando tokens/costo/latencia en CostTracker.

    Usa UsageMetadataCallbackHandler para capturar el uso real reportado por Groq,
    incluso a través de .with_structured_output() (el callback ve la respuesta cruda
    del LLM antes de que se parsee al modelo Pydantic).
    """
    from langchain_core.callbacks import UsageMetadataCallbackHandler

    cb = UsageMetadataCallbackHandler()
    start = time.perf_counter()
    result = await llm.ainvoke(messages, config={"callbacks": [cb]})
    latency = time.perf_counter() - start

    usage = cb.usage_metadata.get(model, {})
    tokens_in = usage.get("input_tokens", 0)
    tokens_out = usage.get("output_tokens", 0)
    if tokens_in == 0 and tokens_out == 0:
        # Sin uso real reportado (p. ej. un LLM mockeado en tests) — no registrar ruido.
        return result

    get_tracker().record(
        agent=agent,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_s=latency,
        model=model,
        session_id=session_id,
    )
    return result
