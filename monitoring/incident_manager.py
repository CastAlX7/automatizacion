"""8.7 Procedimiento ante incidentes — Gestión estructurada de incidentes.

Implementa el flujo de 5 pasos de la plantilla:
1. Detección → 2. Triage → 3. Diagnóstico → 4. Mitigación → 5. Post-mortem

Uso:
    python -m monitoring.incident_manager                    # Detecta y clasifica
    python -m monitoring.incident_manager --list             # Lista incidentes abiertos
    python -m monitoring.incident_manager --resolve INC-001  # Cierra un incidente
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
INCIDENTS_PATH = DATA_DIR / "incidents.json"

CATEGORY_RUNBOOKS = {
    "calidad": {
        "diagnosis": [
            "Revisar trazas LangSmith del periodo afectado",
            "Comparar distribución de respuestas vs baseline",
            "Verificar si hubo cambio de modelo por parte del proveedor",
            "Revisar si el golden set cambió recientemente",
        ],
        "mitigation": [
            "Rollback al prompt de la versión anterior",
            "Cambiar a modelo estable si el actual tiene degradación",
            "Activar degradación controlada: respuestas conservadoras",
        ],
    },
    "latencia": {
        "diagnosis": [
            "Revisar p50/p95/p99 por agente en cost_tracker",
            "Verificar status de Groq (status.groq.com)",
            "Revisar si los prompts crecieron (más tokens = más lento)",
            "Verificar carga del servidor (CPU, memoria)",
        ],
        "mitigation": [
            "Fallback a modelo más rápido (llama-3.1-8b-instant)",
            "Reducir k de retrieval o tamaño de contexto",
            "Activar caché de respuestas para queries repetitivos",
        ],
    },
    "costo": {
        "diagnosis": [
            "Revisar cost_tracker.summary() por agente",
            "Identificar picos de uso (¿scraping masivo? ¿loop infinito?)",
            "Comparar tokens/consulta vs promedio histórico",
        ],
        "mitigation": [
            "Activar rate limiting estricto",
            "Cambiar a modelo más barato para queries simples",
            "Pausar scraping automático si es la causa",
        ],
    },
    "disponibilidad": {
        "diagnosis": [
            "Revisar logs de error en el servidor",
            "Verificar conectividad a Groq API, Airtable, Telegram",
            "Revisar si el checkpointer SQLite está bloqueado",
        ],
        "mitigation": [
            "Reiniciar el servicio Streamlit",
            "Verificar y renovar API keys si expiraron",
            "Fallback: modo degradado sin LLM (solo datos en caché)",
        ],
    },
    "seguridad": {
        "diagnosis": [
            "Revisar monitoring/data/security_log.jsonl",
            "Identificar IPs/usuarios que generan los intentos",
            "Verificar si los guardrails están activos",
        ],
        "mitigation": [
            "Bloquear usuario/IP sospechosa",
            "Reforzar validación de input",
            "Revisar y actualizar patrones de detección",
        ],
    },
    "saturación": {
        "diagnosis": [
            "Revisar tamaño de checkpoints.sqlite",
            "Verificar espacio en disco del servidor",
            "Revisar conexiones activas a SQLite",
        ],
        "mitigation": [
            "Purgar checkpoints antiguos (> 30 días)",
            "Migrar checkpointer a PostgreSQL",
            "Aumentar almacenamiento del servidor",
        ],
    },
}


def _load_incidents() -> list[dict]:
    if not INCIDENTS_PATH.exists():
        return []
    with open(INCIDENTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_incidents(incidents: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(INCIDENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(incidents, f, ensure_ascii=False, indent=2)


def _next_id(incidents: list[dict]) -> str:
    nums = []
    for inc in incidents:
        try:
            nums.append(int(inc["id"].split("-")[1]))
        except (IndexError, ValueError):
            pass
    return f"INC-{(max(nums) + 1) if nums else 1:03d}"


def detect_and_triage() -> list[dict]:
    """Paso 1-2: Detección + Triage automático desde alertas recientes."""
    from monitoring.alerts import AlertMonitor

    monitor = AlertMonitor()
    alerts = monitor.check_all()

    if not alerts:
        print("No alerts fired — no incidents to create.")
        return []

    incidents = _load_incidents()
    new_incidents = []

    for alert in alerts:
        already_open = any(
            inc.get("status") == "open"
            and inc.get("category") == alert["category"]
            and inc.get("metric") == alert["metric"]
            for inc in incidents
        )
        if already_open:
            continue

        runbook = CATEGORY_RUNBOOKS.get(alert["category"], {})

        incident = {
            "id": _next_id(incidents + new_incidents),
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved_at": None,

            "severity": alert["severity"],
            "category": alert["category"],
            "metric": alert["metric"],
            "value": alert["value"],
            "threshold": alert["threshold"],

            "diagnosis_steps": runbook.get("diagnosis", []),
            "mitigation_options": runbook.get("mitigation", []),
            "action_taken": None,
            "root_cause": None,
            "lessons_learned": None,
        }

        incidents.append(incident)
        new_incidents.append(incident)

    _save_incidents(incidents)

    for inc in new_incidents:
        print(f"\n  [{inc['id']}] {inc['severity'].upper()} — {inc['category']}")
        print(f"    Metric: {inc['metric']} = {inc['value']} (threshold: {inc['threshold']})")
        print(f"    Diagnosis:")
        for step in inc["diagnosis_steps"]:
            print(f"      - {step}")
        print(f"    Mitigation options:")
        for opt in inc["mitigation_options"]:
            print(f"      - {opt}")

    return new_incidents


def list_incidents(status: str | None = None) -> list[dict]:
    incidents = _load_incidents()
    if status:
        incidents = [i for i in incidents if i.get("status") == status]

    if not incidents:
        print("No incidents found.")
        return []

    for inc in incidents:
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(inc.get("severity", ""), "⚪")
        print(f"  {icon} [{inc['id']}] {inc['status'].upper()} — {inc['category']}: {inc['metric']}={inc['value']}")
        if inc.get("action_taken"):
            print(f"      Action: {inc['action_taken']}")
        if inc.get("root_cause"):
            print(f"      Root cause: {inc['root_cause']}")

    return incidents


def resolve_incident(
    incident_id: str,
    action_taken: str = "",
    root_cause: str = "",
    lessons: str = "",
) -> None:
    """Paso 4-5: Mitigación + Post-mortem."""
    incidents = _load_incidents()
    found = False
    for inc in incidents:
        if inc["id"] == incident_id:
            inc["status"] = "resolved"
            inc["resolved_at"] = datetime.now(timezone.utc).isoformat()
            inc["action_taken"] = action_taken or "Manual resolution"
            inc["root_cause"] = root_cause
            inc["lessons_learned"] = lessons
            found = True
            print(f"  [{incident_id}] resolved.")
            break

    if not found:
        print(f"  Incident {incident_id} not found.")
        return

    _save_incidents(incidents)


def main():
    parser = argparse.ArgumentParser(description="Incident Manager — Anymotor")
    parser.add_argument("--list", action="store_true", help="List open incidents")
    parser.add_argument("--list-all", action="store_true", help="List all incidents")
    parser.add_argument("--resolve", type=str, help="Resolve incident by ID")
    parser.add_argument("--action", type=str, default="", help="Action taken (with --resolve)")
    parser.add_argument("--cause", type=str, default="", help="Root cause (with --resolve)")
    parser.add_argument("--lessons", type=str, default="", help="Lessons learned (with --resolve)")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    if args.list:
        list_incidents(status="open")
    elif args.list_all:
        list_incidents()
    elif args.resolve:
        resolve_incident(args.resolve, args.action, args.cause, args.lessons)
    else:
        detect_and_triage()


if __name__ == "__main__":
    main()
