# ---------- Build stage ----------
FROM node:24-alpine AS builder

WORKDIR /app

# Install deps first for better caching
COPY package*.json ./
RUN npm ci

# Copy source and config
COPY tsconfig.json ./
COPY index.ts ./index.ts

# Transpile TypeScript -> JavaScript
RUN npm run tsc


# ---------- Runtime stage ----------
FROM node:24-alpine

WORKDIR /app

# Copy only what we need to run
COPY package*.json ./
RUN npm ci --omit=dev

COPY --from=builder /app/dist ./dist

# Run the compiled JS
CMD ["node", "dist/index.js"]
