"use strict";
const router = require("express").Router();
const bcrypt = require("bcrypt");
const jwt = require("jsonwebtoken");
const db = require("../db");

const SALT_ROUNDS = 12;

// ── POST /api/auth/register ───────────────────────────────────────────────────
router.post("/register", async (req, res) => {
  const { username, password, role } = req.body;

  if (!username || !password) {
    return res
      .status(400)
      .json({ error: "username and password are required." });
  }

  const allowedRoles = ["admin", "user"];
  const userRole = allowedRoles.includes(role) ? role : "user";

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
router.post("/login", async (req, res) => {
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
