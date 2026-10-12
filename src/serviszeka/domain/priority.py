"""ITIL etki x aciliyet -> öncelik matrisi."""

from serviszeka.domain.enums import Impact, Priority, Urgency

_MATRIX: dict[tuple[Impact, Urgency], Priority] = {
    (Impact.HIGH, Urgency.HIGH): Priority.P1,
    (Impact.HIGH, Urgency.MEDIUM): Priority.P2,
    (Impact.HIGH, Urgency.LOW): Priority.P3,
    (Impact.MEDIUM, Urgency.HIGH): Priority.P2,
    (Impact.MEDIUM, Urgency.MEDIUM): Priority.P3,
    (Impact.MEDIUM, Urgency.LOW): Priority.P4,
    (Impact.LOW, Urgency.HIGH): Priority.P3,
    (Impact.LOW, Urgency.MEDIUM): Priority.P4,
    (Impact.LOW, Urgency.LOW): Priority.P5,
}


def calculate_priority(impact: Impact, urgency: Urgency) -> Priority:
    return _MATRIX[(impact, urgency)]
