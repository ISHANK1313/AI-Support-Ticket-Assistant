"""Live policy-path evaluation. Offline callers inject a decide callable."""
import argparse
import csv
import json
import sys
from pathlib import Path

# Deliberately explicit, not derived from a historical row or evolving schema.
ALLOWED_INPUT_KEYS = frozenset({
    "message", "order_value_inr", "days_since_delivery", "days_since_dispatch",
    "product_type", "opened_status", "order_status", "ordered_item", "received_item",
    "original_item_available",
})


def select_input(row: dict) -> dict:
    return {key: value for key, value in row.items() if key in ALLOWED_INPUT_KEYS}


def load_rows(path: Path, *, csv_mode: bool = False) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        if csv_mode:
            rows = list(csv.DictReader(source))
            for row in rows:
                for key in ALLOWED_INPUT_KEYS & row.keys():
                    value = row[key]
                    if value == "":
                        row[key] = None
                    elif key in {"days_since_delivery", "days_since_dispatch"}:
                        # Preserve invalid text so validation counts that row as an error.
                        if value.isascii() and value.isdigit():
                            row[key] = int(value)
                    elif key == "original_item_available" and value.lower() in {"true", "false"}:
                        row[key] = value.lower() == "true"
            return rows
        rows = json.load(source)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Cases must be a JSON array of objects")
    return rows


def live_decider():
    """Lazy import prevents CLI help/tests from configuring or contacting Gemini."""
    from src.config import Settings
    from src.decision import DecisionService

    return DecisionService(Settings.load()).decide


def evaluate(rows, decide, *, label_key="expected_action", output=None) -> dict:
    from src.schemas import DecisionOutput, TicketInput

    output = output if output is not None else sys.stdout
    summary = {"total": 0, "correct": 0, "incorrect": 0, "errors": 0, "accuracy": 0.0}
    for number, row in enumerate(rows, start=1):
        summary["total"] += 1
        try:
            expected = row.get(label_key)
            if not isinstance(expected, str) or not expected.strip():
                raise ValueError("Missing expected label")
            ticket = TicketInput(**select_input(row))
            result = decide(ticket)
            # The shared decision path returns DecisionOutput, not unvalidated JSON.
            if not isinstance(result, DecisionOutput):
                raise TypeError("Decision service must return DecisionOutput")
            action = result.action.value
            matched = action == expected.strip()
            summary["correct" if matched else "incorrect"] += 1
            print(f"row={number} {'correct' if matched else 'incorrect'} predicted={action}", file=output)
        except Exception as error:
            summary["errors"] += 1
            # Never print raw provider exceptions, customer identities, or ticket text.
            print(f"row={number} error={type(error).__name__}", file=output)
    if summary["total"]:
        summary["accuracy"] = summary["correct"] / summary["total"]
    print(" ".join(f"{key}={summary[key]}" for key in ("total", "correct", "incorrect", "errors"))
          + f" accuracy={summary['accuracy']:.2%}", file=output)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases", type=Path, help="Supplied JSON cases with expected_action")
    source.add_argument("--csv", type=Path, help="Historical CSV diagnostic label agreement only")
    args = parser.parse_args(argv)
    csv_mode = args.csv is not None
    try:
        rows = load_rows(args.csv if csv_mode else args.cases, csv_mode=csv_mode)
    except (OSError, ValueError, csv.Error):
        print("Cannot load cases: check the input path and JSON/CSV format.", file=sys.stderr)
        return 2
    if csv_mode:
        print("Historical CSV diagnostic label agreement, not held-out accuracy.")
    else:
        print("Supplied visible-case evaluation; not comprehensive correctness evidence.")
    try:
        decide = live_decider() if rows else None
    except Exception:
        print("Decision service unavailable: verify local configuration, dependencies and ingestion.")
        def decide(_):
            raise RuntimeError("Decision service initialization failed")
    summary = evaluate(rows, decide, label_key="resolved_action" if csv_mode else "expected_action")
    return 1 if summary["errors"] or summary["incorrect"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
