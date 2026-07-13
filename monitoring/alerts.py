"""8.6 Monitoreo y alertas — Sistema de alertas accionables.

Monitorea métricas del sistema y envía alertas por Telegram cuando se
exceden umbrales. Cada alerta incluye la métrica, el umbral, el valor
actual y el runbook de respuesta.

Uso como script independiente (cron cada 5 min):
    python -m monitoring.alerts

Uso programático:
    from monitoring.alerts import AlertMonitor
    monitor = AlertMonitor()
    monitor.check_all()
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

DATA_DIR = Path(__file__).resolve().parent / "data"


class AlertMonitor:
    def __init__(self, env: str | None = None) -> None:
        self._env = env or os.getenv("APP_ENV", "dev")
        self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self._alerts_log = DATA_DIR / "alerts_log.jsonl"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_config()

    def _load_config(self) -> None:
        from shared.config_loader import load_config

        cfg = load_config(self._env)
        eval_cfg = cfg.get("evaluation", {})
        rate_cfg = cfg.get("rate_limits", {})
        self.thresholds = {
            "max_latency_p95_s": eval_cfg.get("max_latency_p95_s", 3),
            "max_cost_per_query_usd": eval_cfg.get("max_cost_per_query_usd", 0.01),
            "budget_daily_usd": eval_cfg.get("max_cost_per_query_usd", 0.01)
            * rate_cfg.get("max_requests_per_minute", 30)
            * 60
            * 8,
            "max_error_rate_pct": 10,
            "max_tokens_per_session": rate_cfg.get("max_tokens_per_session", 20000),
            "checkpoint_db_max_mb": 500,
        }

    def check_all(self) -> list[dict[str, Any]]:
        fired: list[dict[str, Any]] = []
        fired.extend(self._check_latency())
        fired.extend(self._check_cost())
        fired.extend(self._check_errors())
        fired.extend(self._check_checkpoint_size())
        fired.extend(self._check_injection_attempts())

        for alert in fired:
            self._log_alert(alert)
            self._send_telegram(alert)

        return fired

    # ── Latencia (p50, p95, p99) ──

    def _check_latency(self) -> list[dict]:
        from monitoring.cost_tracker import CostTracker

        tracker = CostTracker()
        history = tracker.load_history(since_hours=1)
        if len(history) < 3:
            return []

        latencies = sorted(e.get("latency_s", 0) for e in history)
        p95_idx = int(len(latencies) * 0.95)
        p95 = latencies[min(p95_idx, len(latencies) - 1)]
        threshold = self.thresholds["max_latency_p95_s"]

        if p95 > threshold:
            return [
                self._build_alert(
                    severity="warning",
                    metric="latency_p95_s",
                    value=round(p95, 2),
                    threshold=threshold,
                    category="latencia",
                    runbook=(
                        "1. Revisar trazas LangSmith del último periodo\n"
                        "2. Verificar si Groq tiene degradación (status.groq.com)\n"
                        "3. Si persiste: evaluar fallback a modelo más rápido (8b)\n"
                        "4. Revisar tamaño de prompts — reducir contexto si > 4000 tokens"
                    ),
                )
            ]
        return []

    # ── Costo acumulado vs presupuesto ──

    def _check_cost(self) -> list[dict]:
        from monitoring.cost_tracker import CostTracker

        tracker = CostTracker()
        cost_24h = tracker.cost_since(hours=24)
        budget = self.thresholds["budget_daily_usd"]
        if budget <= 0:
            return []

        alerts = []
        pct = (cost_24h / budget) * 100

        if pct >= 100:
            alerts.append(
                self._build_alert(
                    severity="critical",
                    metric="cost_24h_usd",
                    value=round(cost_24h, 4),
                    threshold=budget,
                    category="costo",
                    runbook=(
                        "1. Presupuesto diario EXCEDIDO\n"
                        "2. Revisar qué agente consume más (cost_tracker.summary())\n"
                        "3. Considerar rate limiting inmediato\n"
                        "4. Evaluar modelo más barato para queries simples"
                    ),
                )
            )
        elif pct >= 90:
            alerts.append(
                self._build_alert(
                    severity="warning",
                    metric="cost_24h_pct",
                    value=round(pct, 1),
                    threshold=90,
                    category="costo",
                    runbook="Costo al 90% del presupuesto diario. Monitorear de cerca.",
                )
            )
        elif pct >= 70:
            alerts.append(
                self._build_alert(
                    severity="info",
                    metric="cost_24h_pct",
                    value=round(pct, 1),
                    threshold=70,
                    category="costo",
                    runbook="Costo al 70% del presupuesto. Revisar tendencia.",
                )
            )

        return alerts

    # ── Tasa de error ──

    def _check_errors(self) -> list[dict]:
        from monitoring.cost_tracker import CostTracker

        tracker = CostTracker()
        history = tracker.load_history(since_hours=1)
        if len(history) < 5:
            return []

        error_count = sum(1 for e in history if e.get("metadata", {}).get("error"))
        error_rate = (error_count / len(history)) * 100
        threshold = self.thresholds["max_error_rate_pct"]

        if error_rate > threshold:
            return [
                self._build_alert(
                    severity="critical",
                    metric="error_rate_pct",
                    value=round(error_rate, 1),
                    threshold=threshold,
                    category="disponibilidad",
                    runbook=(
                        "1. Revisar errores en trazas LangSmith\n"
                        "2. Verificar status de Groq API\n"
                        "3. Si es rate limiting: reducir concurrencia\n"
                        "4. Si es error de modelo: rollback al prompt anterior\n"
                        "5. Activar fallback de proveedor si disponible"
                    ),
                )
            ]
        return []

    # ── Saturación del checkpointer ──

    def _check_checkpoint_size(self) -> list[dict]:
        db_path = Path(__file__).resolve().parent.parent / "data" / "checkpoints.sqlite"
        if not db_path.exists():
            return []

        size_mb = db_path.stat().st_size / (1024 * 1024)
        threshold = self.thresholds["checkpoint_db_max_mb"]

        if size_mb > threshold:
            return [
                self._build_alert(
                    severity="warning",
                    metric="checkpoint_db_mb",
                    value=round(size_mb, 1),
                    threshold=threshold,
                    category="saturación",
                    runbook=(
                        "1. Base de checkpoints excede el límite\n"
                        "2. Purgar checkpoints antiguos (> 30 días)\n"
                        "3. Considerar migrar a PostgreSQL si el volumen sigue creciendo"
                    ),
                )
            ]
        return []

    # ── Detección de inyección de prompts ──

    def _check_injection_attempts(self) -> list[dict]:
        log_path = DATA_DIR / "security_log.jsonl"
        if not log_path.exists():
            return []

        cutoff = time.time() - 3600
        recent_attempts = 0
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                    if ts >= cutoff:
                        recent_attempts += 1
                except (json.JSONDecodeError, KeyError):
                    continue

        if recent_attempts >= 3:
            return [
                self._build_alert(
                    severity="critical",
                    metric="injection_attempts_1h",
                    value=recent_attempts,
                    threshold=3,
                    category="seguridad",
                    runbook=(
                        "1. Revisar security_log.jsonl para ver los inputs sospechosos\n"
                        "2. Identificar si es un usuario o un patrón automatizado\n"
                        "3. Considerar bloquear IP/usuario\n"
                        "4. Verificar que guardrails están activos"
                    ),
                )
            ]
        return []

    # ── Helpers ──

    def _build_alert(
        self,
        severity: str,
        metric: str,
        value: float | int,
        threshold: float | int,
        category: str,
        runbook: str,
    ) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": self._env,
            "severity": severity,
            "category": category,
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "runbook": runbook,
        }

    def _log_alert(self, alert: dict) -> None:
        with open(self._alerts_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert, ensure_ascii=False) + "\n")

    def _send_telegram(self, alert: dict) -> None:
        if not self._bot_token or not self._chat_id:
            return

        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(
            alert["severity"], "⚪"
        )
        msg = (
            f"{icon} *ALERTA {alert['severity'].upper()}*\n\n"
            f"Entorno: {alert['environment']}\n"
            f"Categoría: {alert['category']}\n"
            f"Métrica: `{alert['metric']}`\n"
            f"Valor: {alert['value']} (umbral: {alert['threshold']})\n\n"
            f"*Runbook:*\n{alert['runbook']}"
        )

        try:
            requests.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception:
            pass


def main():
    from dotenv import load_dotenv

    load_dotenv()

    monitor = AlertMonitor()
    alerts = monitor.check_all()

    if alerts:
        print(f"\n{len(alerts)} alert(s) fired:")
        for a in alerts:
            print(
                f"  [{a['severity'].upper()}] {a['category']}: {a['metric']}={a['value']} (threshold={a['threshold']})"
            )
    else:
        print("No alerts.")


if __name__ == "__main__":
    main()
