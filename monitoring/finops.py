"""8.8 Escalado y FinOps — Reporte de tendencia de costos y recomendaciones.

Genera un reporte de uso y costos con recomendaciones de optimización:
etiquetado por agente, estrategia de modelos, y límites de uso.

Uso:
    python -m monitoring.finops                 # Reporte de últimas 24h
    python -m monitoring.finops --hours 168     # Última semana
    python -m monitoring.finops --recommend     # Con recomendaciones
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def generate_report(hours: int = 24, with_recommendations: bool = False) -> dict[str, Any]:
    from monitoring.cost_tracker import CostTracker
    from shared.config_loader import load_config

    env = os.getenv("APP_ENV", "dev")
    cfg = load_config(env)
    rate_limits = cfg.get("rate_limits", {})

    tracker = CostTracker()
    history = tracker.load_history(since_hours=hours)

    if not history:
        return {"period_hours": hours, "total_entries": 0, "message": "No data in period"}

    by_agent: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "latencies": [],
    })
    by_model: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "calls": 0, "tokens_total": 0, "cost_usd": 0.0,
    })

    total_cost = 0.0
    total_tokens = 0

    for entry in history:
        agent = entry.get("agent", "unknown")
        model = entry.get("model", "unknown")
        cost = entry.get("cost_usd", 0)
        t_in = entry.get("tokens_in", 0)
        t_out = entry.get("tokens_out", 0)
        lat = entry.get("latency_s", 0)

        by_agent[agent]["calls"] += 1
        by_agent[agent]["tokens_in"] += t_in
        by_agent[agent]["tokens_out"] += t_out
        by_agent[agent]["cost_usd"] += cost
        by_agent[agent]["latencies"].append(lat)

        by_model[model]["calls"] += 1
        by_model[model]["tokens_total"] += t_in + t_out
        by_model[model]["cost_usd"] += cost

        total_cost += cost
        total_tokens += t_in + t_out

    agent_summary = {}
    for agent, data in by_agent.items():
        sorted_lat = sorted(data["latencies"])
        p95_idx = int(len(sorted_lat) * 0.95)
        agent_summary[agent] = {
            "calls": data["calls"],
            "tokens_in": data["tokens_in"],
            "tokens_out": data["tokens_out"],
            "cost_usd": round(data["cost_usd"], 6),
            "cost_pct": round((data["cost_usd"] / total_cost * 100) if total_cost else 0, 1),
            "avg_tokens_per_call": round((data["tokens_in"] + data["tokens_out"]) / data["calls"]) if data["calls"] else 0,
            "latency_p95_s": sorted_lat[min(p95_idx, len(sorted_lat) - 1)] if sorted_lat else 0,
        }

    model_summary = {}
    for model, data in by_model.items():
        model_summary[model] = {
            "calls": data["calls"],
            "tokens_total": data["tokens_total"],
            "cost_usd": round(data["cost_usd"], 6),
        }

    daily_rate = total_cost / (hours / 24) if hours > 0 else 0
    monthly_projection = daily_rate * 30

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": env,
        "period_hours": hours,
        "total_entries": len(history),
        "total_cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
        "avg_cost_per_call_usd": round(total_cost / len(history), 6) if history else 0,
        "daily_run_rate_usd": round(daily_rate, 4),
        "monthly_projection_usd": round(monthly_projection, 2),
        "by_agent": agent_summary,
        "by_model": model_summary,
        "limits": {
            "max_tokens_per_session": rate_limits.get("max_tokens_per_session", "not set"),
            "max_requests_per_minute": rate_limits.get("max_requests_per_minute", "not set"),
        },
    }

    if with_recommendations:
        report["recommendations"] = _generate_recommendations(report, cfg)

    return report


def _generate_recommendations(report: dict, cfg: dict) -> list[str]:
    recs: list[str] = []
    eval_cfg = cfg.get("evaluation", {})
    max_cost = eval_cfg.get("max_cost_per_query_usd", 0.01)

    avg_cost = report.get("avg_cost_per_call_usd", 0)
    if avg_cost > max_cost:
        recs.append(
            f"Costo promedio por consulta (${avg_cost:.4f}) excede umbral (${max_cost}). "
            "Considerar modelo más barato para queries simples (acquisition → 8b para pre-filtro)."
        )

    for agent, data in report.get("by_agent", {}).items():
        if data.get("cost_pct", 0) > 50:
            recs.append(
                f"Agente '{agent}' consume {data['cost_pct']}% del costo total. "
                f"Revisar longitud de prompts ({data['avg_tokens_per_call']} tokens/call promedio)."
            )
        if data.get("latency_p95_s", 0) > eval_cfg.get("max_latency_p95_s", 3):
            recs.append(
                f"Agente '{agent}' tiene latencia p95 de {data['latency_p95_s']}s. "
                "Considerar reducir contexto o cambiar a modelo más rápido."
            )

    monthly = report.get("monthly_projection_usd", 0)
    if monthly > 50:
        recs.append(
            f"Proyección mensual: ${monthly:.2f}. "
            "Implementar caché semántica para consultas repetitivas."
        )

    if len(report.get("by_model", {})) == 1:
        recs.append(
            "Solo se usa un modelo. Estrategia de modelos por tipo: "
            "usar 8b-instant para pre-filtro/CRM y 70b solo para acquisition/closing."
        )

    if not recs:
        recs.append("Sin recomendaciones — costos y latencia dentro de umbrales.")

    return recs


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="FinOps Report — Anymotor")
    parser.add_argument("--hours", type=int, default=24, help="Period in hours (default: 24)")
    parser.add_argument("--recommend", action="store_true", help="Include optimization recommendations")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    report = generate_report(hours=args.hours, with_recommendations=args.recommend)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if report.get("total_entries", 0) == 0:
        print(f"No data in the last {args.hours}h.")
        return

    print(f"\n{'='*55}")
    print(f"  FinOps Report — {report['environment']} ({args.hours}h)")
    print(f"{'='*55}")
    print(f"  Total calls:          {report['total_entries']}")
    print(f"  Total tokens:         {report['total_tokens']:,}")
    print(f"  Total cost:           ${report['total_cost_usd']:.4f}")
    print(f"  Avg cost/call:        ${report['avg_cost_per_call_usd']:.6f}")
    print(f"  Daily run rate:       ${report['daily_run_rate_usd']:.4f}/day")
    print(f"  Monthly projection:   ${report['monthly_projection_usd']:.2f}/month")

    print(f"\n  By Agent:")
    for agent, data in report.get("by_agent", {}).items():
        print(f"    {agent:20s}  {data['calls']:>4} calls  ${data['cost_usd']:.4f} ({data['cost_pct']}%)  p95={data['latency_p95_s']:.2f}s")

    print(f"\n  By Model:")
    for model, data in report.get("by_model", {}).items():
        print(f"    {model:40s}  {data['calls']:>4} calls  ${data['cost_usd']:.4f}")

    if args.recommend and report.get("recommendations"):
        print(f"\n  Recommendations:")
        for i, rec in enumerate(report["recommendations"], 1):
            print(f"    {i}. {rec}")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()
