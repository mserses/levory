"""Katalog araçları (33 + sorun yönetimi 8). Yeni alan modülü burada kayda alınır."""

from serviszeka.tools.builtin import catalog, change, identity, incident, kb, ops, problem

_registered = False


def register_builtin_tools() -> None:
    global _registered
    if _registered:
        return
    for module in (incident, change, kb, ops, problem, catalog, identity):
        module.register_all()
    _registered = True
