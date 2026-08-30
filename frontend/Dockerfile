# Do targets: `dev` (Vite dev server) aur `prod` (built assets + nginx).

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

# npm ci (npm install nahi) — lockfile ko exactly follow karta hai.
# Production build me dependency version chup-chaap badal jaana wo bug hai
# jo sirf deploy ke baad dikhta hai.
COPY package*.json ./
RUN npm ci

COPY . .

# ⚠️ Vite env vars BUILD TIME pe bake hote hain, runtime pe nahi.
# Yaani API URL image ke andar chala jata hai — container start hone par
# badla nahi ja sakta. Isliye ye build arg hai.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS prod

# Sirf built static files — na node_modules, na source. Dev image ~500MB
# ki hai, ye ~50MB.
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
