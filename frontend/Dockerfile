# Next.js frontend. Build context: ./frontend (docker compose) or the /frontend service root (Railway).
# BACKEND_URL is baked into the /api rewrite at build time.
# Stage order matters: platforms that build without a --target (Railway) use the LAST stage = runner.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# Unit (Vitest) + end-to-end (Playwright) tests; runs against the `frontend` service (docker compose --profile test).
FROM mcr.microsoft.com/playwright:v1.52.0-noble AS test
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
CMD ["sh", "-c", "npm run test:unit && npx playwright test"]

FROM node:22-alpine AS build
WORKDIR /app
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=$BACKEND_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
