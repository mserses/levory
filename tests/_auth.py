"""Test yardımcıları: geliştirme kipi bearer belirteçleri."""


def dev_headers(username: str = "test.agent", *roles: str) -> dict[str, str]:
    """`Authorization: Bearer dev:<kullanıcı>:<rol1,rol2>` başlığı (rol verilmezse agent)."""
    return {"Authorization": f"Bearer dev:{username}:{','.join(roles or ('agent',))}"}


AGENT = dev_headers("ayse", "agent")
APPROVER = dev_headers("sef.ali", "approver")
ADMIN = dev_headers("yonetici", "admin")
