"""Hizmet Masası ajanını etiketli veri setiyle değerlendirir.

Kullanım (proje kökünden):
    SZ_ANTHROPIC_API_KEY=... uv run python evals/run_eval.py
    uv run python evals/run_eval.py --oracle      # LLM'siz duman testi (her şey doğru)
    uv run python evals/run_eval.py --limit 20 --model claude-haiku-5-5

Çıktı: evals/results/<model>.json ve .md. Kapı (%70 doğru yönlendirme) geçilmezse çıkış kodu 1.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from serviszeka.config import get_settings
from serviszeka.evaluation import evaluate, load_dataset, render_markdown
from serviszeka.llm import FakeLLM, get_llm
from serviszeka.privacy import mask_pii

HERE = Path(__file__).parent


def _oracle(dataset: list[dict]) -> FakeLLM:
    """Başlığa göre altın etiketi döndüren sahte LLM: boru hattının kendisini sınar."""
    # Ajanın LLM'e gönderdiği (maskelenmiş) metinle eşleştirilir; aynı başlıklı kayıtlar var
    by_text = {
        mask_pii(f"Başlık: {row['title']}\nAçıklama: {row['description']}"): row["labels"]
        for row in dataset
    }

    def respond(system: str, user: str) -> dict:
        labels = next(v for k, v in by_text.items() if k in user)
        return {**labels, "kb_article_ids": [], "confidence": 0.9, "rationale": "oracle"}

    return FakeLLM(respond)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=HERE / "data" / "dataset.jsonl", type=Path)
    parser.add_argument("--model", help="LLM modeli (varsayılan: SZ_LLM_FAST_MODEL)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--oracle", action="store_true", help="LLM yerine altın etiketler")
    parser.add_argument("--out", default=HERE / "results", type=Path)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)[: args.limit]
    settings = get_settings()
    if args.model:
        settings = settings.model_copy(update={"llm_fast_model": args.model})
    llm = _oracle(dataset) if args.oracle else get_llm(settings)
    model = "oracle" if args.oracle else settings.llm_fast_model

    report = await evaluate(dataset, llm, settings=settings, concurrency=args.concurrency)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{model}.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = render_markdown(report, model=model)
    (args.out / f"{model}.md").write_text(md, encoding="utf-8")
    print(md)
    return 0 if report.gate_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
