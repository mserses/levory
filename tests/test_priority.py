from serviszeka.domain.enums import Impact, Priority, Urgency
from serviszeka.domain.priority import calculate_priority


def test_matrix_corners():
    assert calculate_priority(Impact.HIGH, Urgency.HIGH) is Priority.P1
    assert calculate_priority(Impact.LOW, Urgency.LOW) is Priority.P5
    assert calculate_priority(Impact.MEDIUM, Urgency.MEDIUM) is Priority.P3
