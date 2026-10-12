"""OpenAPI şemasını stdout'a yazar (web portalı tip üretimi için).

    uv run python -m serviszeka.openapi_export > portal/openapi.json
"""

from __future__ import annotations

import json
import sys

from serviszeka.main import app


def export() -> str:
    # Sıralı anahtarlar: şema değişmedikçe dosya farkı oluşmaz
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    sys.stdout.buffer.write(export().encode("utf-8"))


if __name__ == "__main__":
    main()
