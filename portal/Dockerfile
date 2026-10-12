# ServisZeka portalı: çok aşamalı derleme -> nginx ile statik SPA
#
#   docker build -t serviszeka-portal \
#     --build-arg VITE_API_BASE_URL=https://api.ornek.com \
#     --build-arg VITE_OIDC_AUTHORITY=https://sso.ornek.com/realms/serviszeka \
#     --build-arg VITE_OIDC_CLIENT_ID=serviszeka-portal .
#   docker run -p 8080:8080 \
#     -e PORTAL_API_ORIGIN=https://api.ornek.com \
#     -e PORTAL_OIDC_ORIGIN=https://sso.ornek.com serviszeka-portal
#
# VITE_* değerleri pakete gömülür (gizli bilgi değildir). PORTAL_*_ORIGIN değerleri çalışma
# zamanında Content-Security-Policy'ye yazılır (connect-src / frame-src / form-action).

# --- 1) Derleme ---
FROM node:22.20.0-alpine3.22 AS build
WORKDIR /app

COPY package.json package-lock.json .npmrc ./
# Tarayıcı indirmesi yok (E2E bu imajda çalışmaz)
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci --no-audit --no-fund

COPY . .

ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_AUTH_MODE=oidc
ARG VITE_OIDC_AUTHORITY=
ARG VITE_OIDC_CLIENT_ID=serviszeka-portal
ARG VITE_OIDC_SCOPE="openid profile email"
ARG VITE_TIME_ZONE=Europe/Istanbul
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_AUTH_MODE=$VITE_AUTH_MODE \
    VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY \
    VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID \
    VITE_OIDC_SCOPE=$VITE_OIDC_SCOPE \
    VITE_TIME_ZONE=$VITE_TIME_ZONE

# Kaynak haritaları üretim imajına girmez
RUN npm run build && find dist -name '*.map' -delete

# --- 2) Sunum ---
FROM nginx:1.28.0-alpine3.21 AS runtime

# CSP'ye yazılacak kökenler (boşluklarla birden çok köken verilebilir)
ENV PORTAL_API_ORIGIN=http://localhost:8000 \
    PORTAL_OIDC_ORIGIN="" \
    NGINX_ENVSUBST_FILTER="^PORTAL_"

RUN rm -f /etc/nginx/conf.d/default.conf \
    && sed -i "/^user /d" /etc/nginx/nginx.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/security-headers.inc.template /etc/nginx/templates/security-headers.inc.template
COPY --from=build /app/dist /usr/share/nginx/html

# nginx root olmayan kullanıcıyla çalışır (8080, yazılabilir dizinler nginx kullanıcısına)
RUN chown -R nginx:nginx /var/cache/nginx /etc/nginx/conf.d /usr/share/nginx/html \
    && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid
USER nginx

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
