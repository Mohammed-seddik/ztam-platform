"use strict";
const router = require("express").Router();
const db = require("../db");
const authenticate = require("../middleware/auth");

// All task routes require a valid JWT
router.use(authenticate);

// ── GET /api/tasks ────────────────────────────────────────────────────────────
// admin → all tasks (with owner username), user → own tasks only
router.get("/", async (req, res) => {
  try {
    let rows;
    if (req.user.role === "admin") {
      [rows] = await db.execute(
        `SELECT t.id, t.title, t.created_at, u.username AS owner
         FROM tasks t
         JOIN users u ON u.id = t.user_id
         ORDER BY t.id DESC`,
      );
    } else {
      [rows] = await db.execute(
        `SELECT t.id, t.title, t.created_at
         FROM tasks t
         WHERE t.user_id = ?
         ORDER BY t.id DESC`,
        [req.user.sub],
      );
    }
    return res.json(rows);
  } catch (err) {
    console.error("GET /tasks error:", err);
    return res.status(500).json({ error: "Internal server error." });
  }
});

// ── POST /api/tasks ───────────────────────────────────────────────────────────
router.post("/", async (req, res) => {
  const { title } = req.body;

  if (!title || !title.trim()) {
    return res.status(400).json({ error: "title is required." });
  }

  try {
    const [result] = await db.execute(
      "INSERT INTO tasks (title, user_id) VALUES (?, ?)",
      [title.trim(), req.user.sub],
    );
    return res.status(201).json({ id: result.insertId, title: title.trim() });
  } catch (err) {
    console.error("POST /tasks error:", err);
    return res.status(500).json({ error: "Internal server error." });
  }
});

// ── DELETE /api/tasks/:id ─────────────────────────────────────────────────────
// admin can delete any task; user only their own
router.delete("/:id", async (req, res) => {
  const taskId = parseInt(req.params.id, 10);

  try {
    let result;
    if (req.user.role === "admin") {
      [result] = await db.execute("DELETE FROM tasks WHERE id = ?", [taskId]);
    } else {
      [result] = await db.execute(
        "DELETE FROM tasks WHERE id = ? AND user_id = ?",
        [taskId, req.user.sub],
      );
    }

    if (result.affectedRows === 0) {
      return res
        .status(404)
        .json({ error: "Task not found or access denied." });
    }
    return res.json({ message: "Task deleted." });
  } catch (err) {
    console.error("DELETE /tasks/:id error:", err);
    return res.status(500).json({ error: "Internal server error." });
  }
});

module.exports = router;
