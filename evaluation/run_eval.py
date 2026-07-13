"""Evaluador del golden set contra los agentes reales.

Uso:
    python -m evaluation.run_eval                    # Corre con Groq real
    python -m evaluation.run_eval --dry-run          # Solo valida el golden set
    python -m evaluation.run_eval --langsmith        # Sube resultados a LangSmith

Requiere GROQ_API_KEY en .env (o como variable de entorno).
Si --langsmith, requiere LANGSMITH_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
GOLDEN_SET_PATH = ROOT / "golden_set.json"
RESULTS_DIR = ROOT / "results"


def load_golden_set() -> list[dict]:
    with open(GOLDEN_SET_PATH, encoding="utf-8") as f:
        return json.load(f)


async def evaluate_acquisition(case: dict, api_key: str, model: str) -> dict[str, Any]:
    from agents.acquisition_agent import AcquisitionAgent
    from shared.graph_state import CarSaleState

    agent = AcquisitionAgent(api_key=api_key, model=model)
    car_data = case["input"]["car_data"]
    inspection_data = case["input"].get("inspection_data", {})
    expected = case["expected"]

    if expected.get("raises") == "ValueError":
        try:
            state = CarSaleState(car_data=car_data, inspection_data=inspection_data)
            await agent(state)
            return {
                "passed": False,
                "reason": "Expected ValueError but none was raised",
            }
        except ValueError:
            return {"passed": True, "reason": "ValueError raised as expected"}

    state = CarSaleState(car_data=car_data, inspection_data=inspection_data)
    start = time.monotonic()
    result = await agent(state)
    latency = time.monotonic() - start

    findings: list[str] = []
    passed = True

    actual_apto = result["car_data"].get("apto_venta")
    if "apto_venta" in expected and actual_apto != expected["apto_venta"]:
        findings.append(
            f"apto_venta: expected {expected['apto_venta']}, got {actual_apto}"
        )
        passed = False

    precio = result["car_data"].get("precio_mercado", 0) or 0
    if "precio_mercado_min" in expected and precio < expected["precio_mercado_min"]:
        findings.append(
            f"precio_mercado {precio} < min {expected['precio_mercado_min']}"
        )
        passed = False
    if "precio_mercado_max" in expected and precio > expected["precio_mercado_max"]:
        findings.append(
            f"precio_mercado {precio} > max {expected['precio_mercado_max']}"
        )
        passed = False

    if "margen_minimo" in expected:
        margen = result["car_data"].get("margen_pct", 0) or 0
        if margen < expected["margen_minimo"]:
            findings.append(f"margen {margen}% < min {expected['margen_minimo']}%")
            passed = False

    if "moneda_detectada_contains" in expected:
        moneda = result["car_data"].get("moneda_detectada", "") or ""
        if expected["moneda_detectada_contains"].lower() not in moneda.lower():
            findings.append(
                f"moneda '{moneda}' no contiene '{expected['moneda_detectada_contains']}'"
            )
            passed = False

    red_flags = [f.lower() for f in (result["car_data"].get("red_flags") or [])]
    for flag in expected.get("should_have_red_flags", []):
        if not any(flag.lower() in rf for rf in red_flags):
            findings.append(f"Missing red flag: '{flag}'")
            passed = False
    for flag in expected.get("should_not_have_red_flags", []):
        if any(flag.lower() in rf for rf in red_flags):
            findings.append(f"Unexpected red flag: '{flag}'")
            passed = False

    green_flags = [f.lower() for f in (result["car_data"].get("green_flags") or [])]
    for flag in expected.get("should_have_green_flags", []):
        if not any(flag.lower() in gf for gf in green_flags):
            findings.append(f"Missing green flag: '{flag}' (soft)")

    return {
        "passed": passed,
        "findings": findings,
        "latency_s": round(latency, 2),
        "precio_mercado": precio,
        "apto_venta": actual_apto,
    }


async def evaluate_publication(case: dict, api_key: str, model: str) -> dict[str, Any]:
    from agents.publication_agent import PublicationAgent
    from shared.graph_state import CarSaleState

    agent = PublicationAgent(api_key=api_key, model=model)
    state = CarSaleState(
        car_data=case["input"]["car_data"],
        inspection_data=case["input"].get("inspection_data", {}),
    )

    start = time.monotonic()
    result = await agent(state)
    latency = time.monotonic() - start

    findings: list[str] = []
    passed = True
    expected = case["expected"]
    pub = result.get("publication_data", {})
    descs = pub.get("descripcion_generada", {})

    if expected.get("has_facebook") and not descs.get("facebook"):
        findings.append("Missing facebook description")
        passed = False
    if expected.get("has_mercadolibre") and not descs.get("mercadolibre"):
        findings.append("Missing mercadolibre description")
        passed = False
    if expected.get("has_instagram") and not descs.get("instagram"):
        findings.append("Missing instagram description")
        passed = False
    if expected.get("titulo_not_empty") and not pub.get("titulo_anuncio"):
        findings.append("Empty titulo_anuncio")
        passed = False
    if "precio_publicar_min" in expected:
        precio = pub.get("precio_publicar", 0)
        if precio < expected["precio_publicar_min"]:
            findings.append(
                f"precio_publicar {precio} < min {expected['precio_publicar_min']}"
            )
            passed = False

    return {"passed": passed, "findings": findings, "latency_s": round(latency, 2)}


async def evaluate_crm(case: dict, api_key: str, model: str) -> dict[str, Any]:
    from agents.crm_chatbot_agent import CRMChatbotAgent
    from shared.graph_state import CarSaleState

    agent = CRMChatbotAgent(api_key=api_key, model=model)
    state = CarSaleState(car_data=case["input"]["car_data"])

    start = time.monotonic()
    result = await agent.handle_message(case["input"]["message"], state)
    latency = time.monotonic() - start

    findings: list[str] = []
    passed = True
    expected = case["expected"]

    if (
        "lead_calificado" in expected
        and result.get("lead_calificado") != expected["lead_calificado"]
    ):
        findings.append(
            f"lead_calificado: expected {expected['lead_calificado']}, got {result.get('lead_calificado')}"
        )
        passed = False

    return {"passed": passed, "findings": findings, "latency_s": round(latency, 2)}


async def evaluate_sales_closing(
    case: dict, api_key: str, model: str
) -> dict[str, Any]:
    from agents.sales_closing_agent import SalesClosingAgent
    from shared.graph_state import CarSaleState

    agent = SalesClosingAgent(api_key=api_key, model=model)
    state = CarSaleState(car_data=case["input"]["car_data"])

    start = time.monotonic()
    result = await agent.negotiate(offer=case["input"]["offer"], state=state)
    latency = time.monotonic() - start

    findings: list[str] = []
    passed = True
    expected = case["expected"]

    if (
        "oferta_aceptable" in expected
        and result.get("oferta_aceptable") != expected["oferta_aceptable"]
    ):
        findings.append(
            f"oferta_aceptable: expected {expected['oferta_aceptable']}, got {result.get('oferta_aceptable')}"
        )
        passed = False
    if (
        "venta_completada" in expected
        and result.get("venta_completada") != expected["venta_completada"]
    ):
        findings.append(
            f"venta_completada: expected {expected['venta_completada']}, got {result.get('venta_completada')}"
        )
        passed = False

    return {"passed": passed, "findings": findings, "latency_s": round(latency, 2)}


EVALUATORS = {
    "acquisition": evaluate_acquisition,
    "publication": evaluate_publication,
    "crm": evaluate_crm,
    "sales_closing": evaluate_sales_closing,
}


async def run_all(
    api_key: str, model: str, upload_langsmith: bool = False
) -> dict[str, Any]:
    cases = load_golden_set()
    results: list[dict] = []
    total_passed = 0
    total_latency = 0.0

    for case in cases:
        case_id = case["id"]
        agent_type = case["agent"]
        evaluator = EVALUATORS.get(agent_type)

        if not evaluator:
            results.append(
                {
                    "id": case_id,
                    "passed": False,
                    "reason": f"Unknown agent type: {agent_type}",
                }
            )
            continue

        print(f"  [{case_id}] {case['description']}...", end=" ", flush=True)
        try:
            outcome = await evaluator(case, api_key, model)
        except Exception as e:
            outcome = {"passed": False, "findings": [f"Exception: {e}"], "latency_s": 0}

        outcome["id"] = case_id
        outcome["agent"] = agent_type
        outcome["description"] = case["description"]
        results.append(outcome)

        if outcome["passed"]:
            total_passed += 1
            print("PASS", f"({outcome.get('latency_s', 0)}s)")
        else:
            print("FAIL", outcome.get("findings", outcome.get("reason", "")))

        total_latency += outcome.get("latency_s", 0)

    total = len(cases)
    accuracy = (total_passed / total * 100) if total > 0 else 0
    avg_latency = (total_latency / total) if total > 0 else 0

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "total_cases": total,
        "passed": total_passed,
        "failed": total - total_passed,
        "accuracy_pct": round(accuracy, 1),
        "avg_latency_s": round(avg_latency, 2),
        "total_latency_s": round(total_latency, 2),
        "results": results,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"eval_{ts}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    baseline = _load_baseline()

    print(f"\n{'=' * 50}")
    print(f"  Accuracy: {total_passed}/{total} ({accuracy:.1f}%)")
    print(f"  Avg latency: {avg_latency:.2f}s")
    print(f"  Results saved: {output_path}")
    if baseline:
        delta = accuracy - baseline["accuracy_pct"]
        arrow = "▲" if delta >= 0 else "▼"
        print(
            f"  vs Baseline: {baseline['accuracy_pct']}% → {accuracy:.1f}% ({arrow} {abs(delta):.1f}pp)"
        )
        summary["baseline_accuracy_pct"] = baseline["accuracy_pct"]
        summary["accuracy_delta_pp"] = round(delta, 1)
        summary["regression"] = delta < 0
    print(f"{'=' * 50}")

    if upload_langsmith:
        await _upload_to_langsmith(cases, results, summary)

    return summary


def _load_baseline() -> dict[str, Any] | None:
    baseline_path = RESULTS_DIR / "baseline.json"
    if not baseline_path.exists():
        return None
    with open(baseline_path, encoding="utf-8") as f:
        return json.load(f)


def save_as_baseline(result_path: str) -> None:
    """Promueve un resultado de evaluación como baseline.

    Uso: python -m evaluation.run_eval --promote evaluation/results/eval_20260713.json
    """
    import shutil

    src = Path(result_path)
    if not src.exists():
        print(f"ERROR: {src} not found")
        sys.exit(1)
    dest = RESULTS_DIR / "baseline.json"
    shutil.copy2(src, dest)
    print(f"  Baseline updated: {dest}")


async def _upload_to_langsmith(
    cases: list[dict], results: list[dict], summary: dict
) -> None:
    try:
        from langsmith import Client

        client = Client()
        dataset_name = "anymotor-golden-set"

        try:
            dataset = client.read_dataset(dataset_name=dataset_name)
        except Exception:
            dataset = client.create_dataset(
                dataset_name=dataset_name,
                description="Golden set de evaluación para Anymotor — autos usados Lima, Perú",
            )

        existing = {
            ex.inputs.get("id"): ex
            for ex in client.list_examples(dataset_id=dataset.id)
        }

        for case in cases:
            if case["id"] not in existing:
                client.create_example(
                    dataset_id=dataset.id,
                    inputs={"id": case["id"], "agent": case["agent"], **case["input"]},
                    outputs=case["expected"],
                    metadata={"description": case["description"]},
                )

        print(f"\n  LangSmith: dataset '{dataset_name}' synced ({len(cases)} cases)")
    except ImportError:
        print("\n  Warning: langsmith not installed, skipping upload")
    except Exception as e:
        print(f"\n  Warning: LangSmith upload failed: {e}")


def validate_golden_set() -> bool:
    cases = load_golden_set()
    valid = True
    for case in cases:
        errors: list[str] = []
        if "id" not in case:
            errors.append("missing 'id'")
        if "agent" not in case:
            errors.append("missing 'agent'")
        if case.get("agent") not in EVALUATORS:
            errors.append(f"unknown agent '{case.get('agent')}'")
        if "input" not in case:
            errors.append("missing 'input'")
        if "expected" not in case:
            errors.append("missing 'expected'")
        if errors:
            print(f"  [{case.get('id', '?')}] INVALID: {', '.join(errors)}")
            valid = False
        else:
            print(f"  [{case['id']}] OK")
    return valid


def main():
    parser = argparse.ArgumentParser(description="Run Anymotor golden set evaluation")
    parser.add_argument(
        "--dry-run", action="store_true", help="Only validate golden set schema"
    )
    parser.add_argument(
        "--langsmith", action="store_true", help="Upload results to LangSmith"
    )
    parser.add_argument("--model", default=None, help="Override GROQ_MODEL")
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0,
        help="Minimum accuracy %% to pass (e.g. 75). Exit 1 if below.",
    )
    parser.add_argument(
        "--env", default="dev", help="Environment label (dev/staging/prod)"
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Load model and thresholds from config/{env}.json",
    )
    parser.add_argument(
        "--promote",
        default=None,
        help="Promote an eval result file as the new baseline",
    )
    parser.add_argument(
        "--block-regression",
        action="store_true",
        help="Exit 1 if accuracy is lower than baseline",
    )
    args = parser.parse_args()

    if args.promote:
        save_as_baseline(args.promote)
        sys.exit(0)

    if args.dry_run:
        print("Validating golden set...")
        valid = validate_golden_set()
        sys.exit(0 if valid else 1)

    if args.config:
        from shared.config_loader import load_config, get_eval_thresholds

        cfg = load_config(args.env)
        model = args.model or cfg.get(
            "model", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        )
        thresholds = get_eval_thresholds(args.env)
        min_accuracy = args.min_accuracy or thresholds.get("min_accuracy_pct", 0)
    else:
        model = args.model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        min_accuracy = args.min_accuracy

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set")
        sys.exit(1)

    print(f"Environment: {args.env}")
    print(f"Running evaluation with model: {model}")
    print(f"Min accuracy threshold: {min_accuracy}%")
    print(f"Block on regression: {'yes' if args.block_regression else 'no'}")
    print(f"LangSmith upload: {'yes' if args.langsmith else 'no'}\n")

    summary = asyncio.run(run_all(api_key, model, upload_langsmith=args.langsmith))

    if min_accuracy > 0 and summary["accuracy_pct"] < min_accuracy:
        print(
            f"\n  BLOCKED: accuracy {summary['accuracy_pct']}% < threshold {min_accuracy}%"
        )
        sys.exit(1)

    if args.block_regression and summary.get("regression"):
        print(
            f"\n  BLOCKED: regression detected ({summary.get('accuracy_delta_pp')}pp vs baseline)"
        )
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
