FROM node:20-alpine

WORKDIR /app

# Copy frontend (server.js uses path.join(__dirname, "..", "frontend"))
COPY frontend/ ./frontend/

# Install backend dependencies
COPY backend/package*.json ./backend/
WORKDIR /app/backend
RUN npm install --omit=dev

# Copy remaining backend source
COPY backend/ .

# Run as non-root (node user is built into node:alpine images)
USER node

EXPOSE 3000
CMD ["node", "server.js"]
