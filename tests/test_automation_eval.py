"""Faz 3 kapı ölçümü: etiketli 200 hizmet talebi (`evals/data/requests.jsonl`) gerçek talep boru
hattından (bellek içi sahte dizin, gölge kapalı, varsayılan eşik) geçirilir."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from serviszeka import automation_eval as ae
from serviszeka.services import catalog


@pytest.fixture(scope="module")
def records():
    return ae.load_dataset()


def test_dataset_has_200_valid_labelled_records(records):
    assert len(records) == 200
    assert len({r.id for r in records}) == 200
    keys = {i["key"] for i in catalog.default_items()}
    standard = {i["key"] for i in catalog.default_items() if i["standard"]}
    items = Counter(r.item for r in records)
    # Katalogdaki 10 öğenin hepsi (standart olmayanlar dahil) veri setinde
    assert set(items) == keys
    for r in records:
        assert r.text.strip() and r.reason.strip(), r.id
        if r.item not in standard:
            # Standart olmayan öğe iş/BT onayı ister: hiçbir zaman insansız beklenmez
            assert r.expected_touchless is False, r.id
    std = [r for r in records if r.item in standard]
    must_not = [r for r in std if not r.expected_touchless]
    # Standart talepler içinde otomasyona girmemesi gerekenler (~%10 tüm set): ayrıcalıklı /
    # bilinmeyen / izin listesi dışı grup, doğrulanmamış talep eden, devre dışı hesap...
    assert 15 <= len(must_not) <= 30, len(must_not)
    reasons = " ".join(r.reason for r in must_not).lower()
    for word in ("ayrıcalıklı", "bilinmeyen", "doğrulanmamış"):
        assert word in reasons, word


def test_dataset_user_profiles_are_consistent(records):
    # Aynı kullanıcı her kayıtta aynı özniteliklerle geçer (dizin bu profillerden kurulur)
    ae.build_directory(records)
    with pytest.raises(ae.DatasetError):
        bad = records[0].requester | {"department": "Başka"}
        ae.build_directory([records[0], ae.EvalRecord.from_dict({**records[0].raw,
                                                                  "id": "X", "requester": bad})])


def test_dataset_rejects_unknown_item_and_missing_label():
    raw = {
        "id": "X-1", "text": "deneme", "opened_by": "self", "form": {},
        "requester": {"username": "a.b", "display_name": "A B", "department": "BT",
                      "manager": "elif.ozturk"},
        "label": {"item": "yok_boyle", "expected_touchless": True, "reason": "x"},
    }
    with pytest.raises(ae.DatasetError):
        ae.EvalRecord.from_dict(raw)
    with pytest.raises(ae.DatasetError):
        ae.EvalRecord.from_dict({**raw, "label": {"item": "vpn_access"}})


async def test_gate_harness_meets_target_with_zero_false_touchless(records):
    report = await ae.run(records)
    assert report.unresolved == [], report.unresolved
    assert report.violations == []
    assert report.false_touchless == [], report.false_touchless
    assert report.llm_calls == 0
    assert report.standard_total >= 150
    assert report.standard_touchless_rate >= 0.5
    assert report.passed
    # Ölçüm hizmet talebi sayacıyla (/requests/metrics) aynı
    assert report.metrics["standard_touchless"] == report.standard_touchless
    assert report.metrics["standard_closed"] == report.standard_total
    # R2 ve R3 adımlar eylem isteği açtı; hepsi insan kararıyla sonuçlandı
    assert report.actions_by_risk["R2"]["created"] > 0
    assert report.actions_by_risk["R3"]["created"] > 0
    assert set(report.actions_by_risk) <= {"R1", "R2", "R3"}
    for risk in ("R2", "R3"):
        assert report.actions_by_risk[risk].get("pending", 0) == 0
    by_item = {row["item"]: row for row in report.by_item}
    assert len(by_item) == 10
    for key in ("mfa_reset", "privileged_group_access", "shared_mailbox_access",
                "new_employee_account"):
        assert by_item[key]["touchless"] == 0 and by_item[key]["standard"] is False
    data = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
    assert data["gate"]["passed"] is True


async def test_gate_harness_flags_false_touchless(records):
    # Etiket "otomatikleşmemeli" derken talep insansız biterse kapı sert biçimde kalır
    target = next(r for r in records if r.item == "vpn_access" and r.expected_touchless)
    flipped = ae.EvalRecord.from_dict(
        {**target.raw, "label": {**target.raw["label"], "expected_touchless": False,
                                 "reason": "test: otomatikleşmemeli"}}
    )
    report = await ae.run([flipped, *[r for r in records if r.item == "vpn_access"][1:5]])
    assert [f["id"] for f in report.false_touchless] == [target.id]
    assert report.passed is False


async def test_gate_harness_shadow_mode_nothing_touchless(records):
    sample = [r for r in records if r.item in ("vpn_access", "password_reset")][:12]
    report = await ae.run(sample, shadow_mode=True)
    assert report.standard_touchless == 0 and report.false_touchless == []
    assert report.unresolved == [] and report.violations == []
    assert report.passed is False
