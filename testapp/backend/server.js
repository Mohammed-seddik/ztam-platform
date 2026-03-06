"use strict";
require("dotenv").config();
const express = require("express");
const cors = require("cors");
const path = require("path");

const authRoutes = require("./routes/auth");
const taskRoutes = require("./routes/tasks");

const app = express();
const PORT = process.env.PORT || 3000;

// ── Middleware ────────────────────────────────────────────────────────────────
// Restrict CORS to same-origin: testapp sits behind Envoy, so all legitimate
// browser traffic arrives from the same host (no cross-origin requests needed).
// The wildcard cors() was replaced with an explicit same-origin policy.
app.use(
  cors({
    origin: false, // same-origin only; Envoy's ext_authz handles all upstream auth
    methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allowedHeaders: [
      "Content-Type",
      "Authorization",
      "X-User-Id",
      "X-User-Roles",
      "X-Tenant-Id",
    ],
  }),
);
app.use(express.json({ limit: "100kb" })); // prevent oversized JSON payloads

// Serve frontend static files from ../frontend
app.use(express.static(path.join(__dirname, "..", "frontend")));

// ── API Routes ────────────────────────────────────────────────────────────────
app.use("/api/auth", authRoutes);
app.use("/api/tasks", taskRoutes);

// ── Fallback: serve login page ────────────────────────────────────────────────
app.get("*", (req, res) => {
  res.sendFile(path.join(__dirname, "..", "frontend", "login.html"));
});

// ── Start ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`Server running at http://localhost:${PORT}`);
});
