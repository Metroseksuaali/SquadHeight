# --- Stage 1: build the SquadCalc static site ---------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY SquadCalc/ ./
# dotenv-webpack reads a .env file at build time; bake in our local API_URL.
COPY env.docker ./.env
RUN npm ci && npm run build

# --- Stage 2: serve the site + our heightmaps, proxy the rest to production ----
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Our heightmaps are mounted at /heightmaps (see docker-compose.yml).
EXPOSE 8080
