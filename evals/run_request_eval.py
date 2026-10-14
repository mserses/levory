"""Faz 3 kapısı: standart taleplerin insansız çözüm oranı (hedef %50).

Bellek içi SQLite + sahte dizin (`InMemoryDirectory.demo()`) ile gerçekçi talep karışımını uçtan
uca çalıştırır (ağ yok, LLM yok). Kullanım (proje kökünden):

    uv run python evals/run_request_eval.py            # özet + kapı
    uv run python evals/run_request_eval.py --json     # ayrıntılı JSON

Kapı geçilmezse ya da güvenlik değişmezi bozulursa çıkış kodu 1.
"""

import argparse
import asyncio
import json
import sys

from serviszeka import db
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.setup import build_fake, install
from serviszeka.request_evaluation import run_scenario
from serviszeka.tools.connectors import connectors


async def main(as_json: bool) -> int:
    db.init_engine("sqlite+aiosqlite:///:memory:")
    await db.create_all()
    install(connectors, build_fake(InMemoryDirectory.demo()))
    try:
        async with db.get_sessionmaker()() as session:
            result = await run_scenario(session)
            await session.commit()
    finally:
        await db.dispose_engine()
    m = result.metrics
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        for o in result.outcomes:
            flag = "insansız" if o["touchless"] else "-"
            print(f"{o['number']} {o['item']:<26} {o['state']:<10} {flag:<9} {o['reason'] or ''}")
        print(
            f"\nStandart: {m.standard_touchless}/{m.standard_closed} insansız "
            f"({m.standard_touchless_rate:.0%}; hedef %50) -> "
            f"{'GEÇTİ' if m.meets_gate else 'KALDI'}"
        )
        for v in result.violations:
            print(f"İHLAL: {v}")
    return 0 if m.meets_gate and not result.violations else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args().json)))
