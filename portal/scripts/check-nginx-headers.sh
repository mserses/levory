#!/bin/sh
# Çalışan portal imajının güvenlik başlıklarını denetler:
#   docker run -d -p 8080:8080 serviszeka-portal && scripts/check-nginx-headers.sh http://127.0.0.1:8080
# Portal hiçbir yerde çerçevelenemez; tek istisna OIDC sessiz yenileme sayfası (/auth/silent).
set -u
BASE="${1:-http://127.0.0.1:8080}"
fail=0
head_of() { curl -sS -I "$BASE$1"; }
has() {
  if printf '%s' "$2" | grep -qi -- "$3"; then echo "ok   $1: $3"; else echo "HATA $1: '$3' yok"; fail=1; fi
}

silent=$(head_of "/auth/silent?code=abc&state=xyz")
has /auth/silent "$silent" "HTTP/1.1 200"
has /auth/silent "$silent" "frame-ancestors 'self'"
has /auth/silent "$silent" "X-Frame-Options: SAMEORIGIN"
has /auth/silent "$silent" "script-src 'self';"

for path in / /kayitlar/abc /auth/callback /index.html /auth/silentx; do
  r=$(head_of "$path")
  has "$path" "$r" "HTTP/1.1 200"
  has "$path" "$r" "frame-ancestors 'none'"
  has "$path" "$r" "X-Frame-Options: DENY"
  has "$path" "$r" "Cache-Control: no-cache"
  has "$path" "$r" "X-Content-Type-Options: nosniff"
done

asset=$(curl -sS "$BASE/" | grep -o '/assets/[^"]*\.js' | head -n 1)
if [ -n "$asset" ]; then
  a=$(head_of "$asset")
  has "$asset" "$a" "immutable"
  has "$asset" "$a" "frame-ancestors 'none'"
else
  echo "HATA index.html içinde /assets/*.js yok"; fail=1
fi
has "kaynak haritası" "$(head_of /assets/index.js.map)" "HTTP/1.1 404"
has "gizli dosya" "$(head_of /.env)" "HTTP/1.1 404"

exit $fail
