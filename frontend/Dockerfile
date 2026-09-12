# Build targets: `dev` (Vite dev server) and `prod` (built assets + nginx).

# ---------------------------------------------------------------------------
FROM node:20-alpine AS dev

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]

# ---------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Use npm ci (not npm install) to strictly follow the lockfile.
# Silent dependency version changes in production builds are bugs
# that only appear after deployment.
COPY package*.json ./
RUN npm ci

COPY . .

# ⚠️ Vite env vars are baked in at BUILD TIME, not runtime.
# This means the API URL is embedded in the image and cannot be
# changed after the container starts. Hence, it is a build arg.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS prod

# Only built static files — no node_modules or source. Dev image is ~500MB,
# while this is ~50MB.
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
