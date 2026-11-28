"""Explicit, one-time changes to Upsell report attribution, not cash dates."""
import json
from pathlib import Path


TRANSFERS = Path(__file__).parent / "approved-plans" / "upsell-cycle-transfers.json"


def load():
    return json.loads(TRANSFERS.read_text(encoding="utf-8"))


def sale_key(row):
    return int(row["id"]), int(row.get("crm_charge_id") or 0)


def apply(rows, cycle_start, cycle_end, transfers=None):
    """Move only the approved package charges and preserve their paid dates."""
    result = list(rows)
    period = str(cycle_start), str(cycle_end)
    for transfer in load() if transfers is None else transfers:
        source = transfer["from_start"], transfer["from_end"]
        target = transfer["to_start"], transfer["to_end"]
        keys = {sale_key(row) for row in transfer["sales"]}
        if period == source:
            result = [row for row in result if sale_key(row) not in keys]
        elif period == target:
            # Old archives lack charge IDs. Replace only the explicitly
            # identified legacy duplicate, never every sale by that student.
            legacy = transfer.get("legacy_replacements", [])
            result = [row for row in result
                      if sale_key(row) not in keys and not (
                          not row.get("crm_charge_id") and any(
                              all(row.get(k) == v for k, v in alias.items())
                              for alias in legacy))]
            result.extend(dict(row, report_cycle_start=target[0],
                               report_cycle_end=target[1],
                               approved_cycle_transfer=transfer["id"])
                          for row in transfer["sales"])
    return result
