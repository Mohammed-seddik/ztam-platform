"use strict";
const router = require("express").Router();
const bcrypt = require("bcrypt");
const jwt = require("jsonwebtoken");
const db = require("../db");

const SALT_ROUNDS = 12;

// ── Simple in-process rate limiter ────────────────────────────────────────────
// Limits both register and login attempts per source IP.
// For production with multiple Node processes, replace with Redis-backed limiter.
const _rlMap = new Map(); // ip → { count, resetAt }
const RATE_WINDOW_MS = 60_000;
const RATE_LIMIT = 10;

function checkRateLimit(ip) {
  const now = Date.now();
  let entry = _rlMap.get(ip);
  if (!entry || now >= entry.resetAt) {
    _rlMap.set(ip, { count: 1, resetAt: now + RATE_WINDOW_MS });
    return true;
  }
  if (entry.count >= RATE_LIMIT) return false;
  entry.count += 1;
  return true;
}

function getClientIp(req) {
  // x-forwarded-for is set by Envoy; fall back to socket address
  const xff = req.headers["x-forwarded-for"];
  return (
    (xff ? xff.split(",")[0].trim() : null) ||
    req.socket.remoteAddress ||
    "unknown"
  );
}

// ── POST /api/auth/register ───────────────────────────────────────────────────
router.post("/register", async (req, res) => {
  if (!checkRateLimit(getClientIp(req))) {
    return res
      .status(429)
      .json({ error: "Too many requests. Please try again later." });
  }

  const { username, password } = req.body;

  if (!username || !password) {
    return res
      .status(400)
      .json({ error: "username and password are required." });
  }

  // Server-side input length limits (prevent oversized-payload abuse)
  if (
    typeof username !== "string" ||
    username.trim().length < 3 ||
    username.trim().length > 50
  ) {
    return res.status(400).json({ error: "Username must be 3–50 characters." });
  }
  if (
    typeof password !== "string" ||
    password.length < 8 ||
    password.length > 200
  ) {
    return res
      .status(400)
      .json({ error: "Password must be 8–200 characters." });
  }

  // Role is always 'user' — never trust client-supplied role
  const userRole = "user";

  try {
    const hash = await bcrypt.hash(password, SALT_ROUNDS);
    const [result] = await db.execute(
      "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
      [username.trim(), hash, userRole],
    );
    return res
      .status(201)
      .json({ message: "User registered.", userId: result.insertId });
  } catch (err) {
    if (err.code === "ER_DUP_ENTRY") {
      return res.status(409).json({ error: "Username already taken." });
    }
    console.error("Register error:", err);
    return res.status(500).json({ error: "Internal server error." });
  }
});

// ── POST /api/auth/login ──────────────────────────────────────────────────────
// NOTE: This route is never hit from outside — Envoy rewrites POST /api/auth/login
// to auth-middleware /login-proxy before forwarding. It exists as a fallback only.
router.post("/login", async (req, res) => {
  if (!checkRateLimit(getClientIp(req))) {
    return res
      .status(429)
      .json({ error: "Too many requests. Please try again later." });
  }

  const { username, password } = req.body;

  if (!username || !password) {
    return res
      .status(400)
      .json({ error: "username and password are required." });
  }

  try {
    const [rows] = await db.execute(
      "SELECT id, username, password_hash, role FROM users WHERE username = ?",
      [username.trim()],
    );

    if (rows.length === 0) {
      return res.status(401).json({ error: "Invalid credentials." });
    }

    const user = rows[0];
    const match = await bcrypt.compare(password, user.password_hash);
    if (!match) {
      return res.status(401).json({ error: "Invalid credentials." });
    }

    const token = jwt.sign(
      { sub: user.id, username: user.username, role: user.role },
      process.env.JWT_SECRET,
      { expiresIn: process.env.JWT_EXPIRES_IN || "1h" },
    );

    return res.json({ token, username: user.username, role: user.role });
  } catch (err) {
    console.error("Login error:", err);
    return res.status(500).json({ error: "Internal server error." });
  }
});

module.exports = router;
