"""Faz 3 kapısı (etiketli veri seti): standart taleplerin insansız çözüm oranı ve yanlış insansız.

`evals/data/requests.jsonl` (200 etiketli Türkçe hizmet talebi) gerçek talep boru hattından, bellek
içi sahte dizinle (ağ yok), gölge mod kapalı ve varsayılan güven eşiğiyle geçirilir. LLM kullanılmaz
(karşılama deterministik katalog planıdır; çağrılırsa sayılır ve kapı kalır). Kullanım (proje
kökünden):

    uv run python evals/run_automation_eval.py                 # özet + kapı
    uv run python evals/run_automation_eval.py --json          # ayrıntılı JSON
    uv run python evals/run_automation_eval.py --save          # evals/results/automation.json
    uv run python evals/run_automation_eval.py --stack keycloak   # Keycloak + Slack biçimli sahte
    uv run python evals/run_automation_eval.py --stack entra      # Entra ID / Graph biçimli sahte

Çıkış kodu 1: oran < %50, yanlış insansız > 0, güvenlik değişmezi ihlali, kapanmamış talep ya da LLM
çağrısı.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from serviszeka.automation_eval import DATASET_PATH, load_dataset, run
from serviszeka.connectors.fake_stacks import STACKS

RESULTS = Path(__file__).resolve().parent / "results" / "automation.json"


def _print_summary(report) -> None:
    g = report.to_dict()["gate"]
    print(f"Veri seti: {DATASET_PATH.name} ({report.total} talep)")
    print(f"Bağlayıcı yığını: {report.settings.get('stack', 'memory')}")
    s = report.settings
    print(f"Politika: gölge mod {'açık' if s['ai_shadow_mode'] else 'kapalı'}, "
          f"güven eşiği {s['ai_confidence_threshold']}, karşılama güveni "
          f"{s['fulfilment_confidence']}, LLM çağrısı {report.llm_calls}")
    print("\nÖğe bazında:")
    print(f"  {'öğe':<26} {'std':<4} {'n':>3} {'insansız':>8} {'oran':>6} {'beklenen':>8}  durumlar")
    for row in report.by_item:
        states = ", ".join(f"{k}={v}" for k, v in row["states"].items())
        print(f"  {row['item']:<26} {'evet' if row['standard'] else 'hayır':<4} {row['total']:>3} "
              f"{row['touchless']:>8} {row['touchless_rate']:>6.0%} "
              f"{row['expected_touchless']:>8}  {states}")
    print("\nEylem istekleri (risk sınıfına göre):")
    for risk, counts in report.actions_by_risk.items():
        rest = ", ".join(f"{k}={v}" for k, v in counts.items() if k != "created")
        print(f"  {risk}: {counts['created']} açıldı ({rest})")
    print(f"\nEtiket uyumu (beklenen insansız == gerçekleşen): {report.label_agreement:.1%}")
    if report.missed_automation:
        print(f"Kaçırılan otomasyon (beklenen insansız, olmadı): {len(report.missed_automation)}")
        for o in report.missed_automation:
            print(f"  {o['id']} {o['item']} {o['state']}: {o['detail']}")
    for o in report.false_touchless:
        print(f"YANLIŞ İNSANSIZ: {o['id']} {o['item']} — etiket: {o['label_reason']}")
    for v in report.violations:
        print(f"İHLAL: {v}")
    for o in report.unresolved:
        print(f"KAPANMADI: {o['id']} {o['item']} {o['state']}")
    print(
        f"\nStandart: {g['standard_touchless']}/{g['standard_total']} insansız "
        f"({g['standard_touchless_rate']:.1%}; hedef %{int(g['target'] * 100)}) | yanlış insansız "
        f"{g['false_touchless']} | ihlal {g['violations']} | kapanmayan {g['unresolved']} -> "
        f"{'GEÇTİ' if g['passed'] else 'KALDI'}"
    )


async def main(as_json: bool, save: bool, stack: str = "memory") -> int:
    report = await run(load_dataset(), stack=stack)
    data = report.to_dict(outcomes=True)
    if save:
        out = RESULTS if stack == "memory" else RESULTS.with_name(f"automation_{stack}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--stack", choices=STACKS, default="memory",
                        help="sahte bağlayıcı yığını (varsayılan memory)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.json, args.save, args.stack)))
