from serviszeka.evaluation import TicketResult, evaluate, render_markdown, summarize
from serviszeka.llm import FakeLLM

GOLD = {
    "kind": "incident",
    "category": "ag",
    "service": "vpn",
    "assignment_group": "network_ops",
    "impact": "low",
    "urgency": "medium",
}


def _row(i, title, labels=GOLD):
    return {"id": f"T-{i}", "title": title, "description": f"{title} açıklama", "labels": labels}


def test_summarize_metrics():
    wrong_group = {**GOLD, "assignment_group": "service_desk_l1"}
    wrong_urgency = {**GOLD, "urgency": "high"}
    results = [
        TicketResult("a", GOLD, dict(GOLD), "suggested", 0.9, 0.1),
        TicketResult("b", GOLD, wrong_group, "suggested", 0.9, 0.1),
        TicketResult("c", GOLD, wrong_urgency, "suggested", 0.9, 0.1),
        TicketResult("d", GOLD, {}, "error", 0.0, 0.1, error="boom"),
    ]
    report = summarize(results)
    assert report.routing_accuracy == 0.5  # a ve c
    assert report.exact_match == 0.25
    assert report.priority_accuracy == 0.5  # a ve b (P4); c P3, d yok
    assert report.field_accuracy["assignment_group"] == 0.5
    assert report.errors == 1
    assert report.gate_passed is False
    assert "KALDI" in render_markdown(report)


async def test_evaluate_runs_real_triage_in_shadow_mode():
    dataset = [_row(1, "VPN bağlanmıyor"), _row(2, "Wi-Fi yok")]

    def respond(system, user):
        # İkinci kayıtta yanlış kategori (sınıf listesinde yok)
        category = "ag" if "VPN" in user else "Network"
        return {**GOLD, "category": category, "kb_article_ids": [], "confidence": 0.9}

    report = await evaluate(dataset, FakeLLM(respond), concurrency=2)
    assert report.total == 2
    assert report.routing_accuracy == 0.5
    assert report.outcomes == {"suggested": 1, "escalated": 1}
    by_id = {t["id"]: t for t in report.tickets}
    assert by_id["T-2"]["predicted"]["category"] is None
