FROM node:20-alpine

WORKDIR /app

# Copy frontend (server.js uses path.join(__dirname, "..", "frontend"))
COPY frontend/ ./frontend/

# Install backend dependencies
COPY backend/package*.json ./backend/
WORKDIR /app/backend
RUN npm install

# Copy remaining backend source
COPY backend/ .

RUN addgroup -S appuser && adduser -S -G appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 3000
CMD ["node", "server.js"]
