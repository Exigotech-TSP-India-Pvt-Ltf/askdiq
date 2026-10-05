# Next.js frontend container — multi-stage build using `output: "standalone"`
# (next.config.js) so the runtime image only ships the traced production
# server, not the whole node_modules tree.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
# API_BASE_URL and API_KEY are read at request time by app/api/proxy's route
# handler (server-only, never bundled into client JS) — pass them as runtime
# environment variables (see docker-compose.yml), not build args.
CMD ["node", "server.js"]
