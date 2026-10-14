"""Faz 4 kanıtı: MTTR düşüşü veriden hesaplanabilir mi? — **SENTETİK** önce/sonra senaryosu.

Gerçek veri DEĞİLDİR. `serviszeka.mttr_evidence` bellek içi SQLite'ta deterministik olaylar üretir
(hedef grup `network_ops`: önceki pencerede yavaş, iyileştirmeden sonra hızlı; kontrol grubu `dba`
sabit), MTTR'yi Faz 4 KPI formülleriyle (`services/kpi.py`: ort(resolved_at − created_at −
sla_paused_seconds), önceki eşit pencereyle) hesaplar, bağımsız aritmetikle karşılaştırır ve aynı
düşüşü sürekli iyileştirme kaydının önce/sonra ölçümüyle (`services/improvements.measure`) doğrular.
Ağ ve LLM yok. Kullanım (proje kökünden):

    uv run python evals/run_mttr_evidence.py                  # özet + kapı
    uv run python evals/run_mttr_evidence.py --json           # ayrıntılı JSON
    uv run python evals/run_mttr_evidence.py --save           # evals/results/mttr_evidence.json
    uv run python evals/run_mttr_evidence.py --now 2026-09-30T12:00:00+00:00 --days 30

Çıkış kodu 1: KPI ile bağımsız hesap uyuşmuyor, düşüş yok, kontrol grubu oynadı ya da CSI ölçümü
KPI raporundan farklı.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from serviszeka.mttr_evidence import CONTROL_GROUP, TARGET_GROUP, run

RESULTS = Path(__file__).resolve().parent / "results" / "mttr_evidence.json"


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def _print_summary(data: dict) -> None:
    print("*** SENTETİK VERİ — gerçek ölçüm değildir; KPI formüllerinin uçtan uca sınamasıdır ***")
    print(
        f"Pencere: {data['window_days']} gün, bitiş {data['now']}; iyileştirme önceki pencerenin "
        "sonunda uygulandı"
    )
    ref, k = data["reference"], data["kpi"]
    print("\nMTTR (saat; etkin süre = duvar saati − SLA duraklatması):")
    print(f"  {'kapsam':<24} {'önce':>8} {'sonra':>8} {'Δ%':>8}  kaynak")
    print(
        f"  {TARGET_GROUP + ' (hedef)':<24} {_fmt(k['group']['previous']):>8} "
        f"{_fmt(k['group']['value']):>8} {_fmt(k['group']['delta_pct']):>8}  kpi.report"
    )
    print(
        f"  {TARGET_GROUP + ' (hedef)':<24} {_fmt(ref['before_hours']):>8} "
        f"{_fmt(ref['after_hours']):>8} {_fmt(ref['delta_pct']):>8}  bağımsız aritmetik"
    )
    print(
        f"  {CONTROL_GROUP + ' (kontrol)':<24} {_fmt(k['control']['previous']):>8} "
        f"{_fmt(k['control']['value']):>8} {_fmt(k['control']['delta_pct']):>8}  kpi.report"
    )
    print(
        f"  {'tümü':<24} {_fmt(k['overall']['previous']):>8} {_fmt(k['overall']['value']):>8} "
        f"{_fmt(k['overall']['delta_pct']):>8}  kpi.report ({k['overall']['trend']})"
    )
    imp = data["improvement"]
    m = imp["measurement"] or {}
    print(
        f"\nSürekli iyileştirme {imp['number']} ({imp['state']}): taban "
        f"{_fmt(imp['baseline_value'])} sa, hedef {_fmt(imp['target_value'])} sa, ölçülen "
        f"{_fmt(imp['measured_value'])} sa, Δ%{_fmt(m.get('delta_pct'))}, hedefe ulaşıldı: "
        f"{'evet' if imp['target_met'] else 'hayır'}"
    )
    print("\nKontroller:")
    for name, ok in data["gate"]["checks"].items():
        print(f"  [{'x' if ok else ' '}] {name}")
    g = k["group"]
    print(
        f"\nMTTR {TARGET_GROUP}: {_fmt(g['previous'])} sa -> {_fmt(g['value'])} sa "
        f"(%{_fmt(g['delta_pct'])}; {g['previous_count']} -> {g['count']} olay, SENTETİK) -> "
        f"{'GEÇTİ' if data['gate']['passed'] else 'KALDI'}"
    )


async def main(as_json: bool, save: bool, now: datetime | None, days: int) -> int:
    report = await run(now=now, days=days)
    data = report.to_dict()
    if save:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_summary(data)
    return 0 if report.passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        default=None,
        help="pencere sonu (ISO 8601; varsayılan şimdi)",
    )
    parser.add_argument("--days", type=int, default=30, help="pencere uzunluğu (gün, 7-180)")
    args = parser.parse_args()
    if not 7 <= args.days <= 180:
        parser.error("--days 7-180 aralığında olmalı")
    sys.exit(asyncio.run(main(args.json, args.save, args.now, args.days)))
