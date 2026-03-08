"use strict";
const jwt = require("jsonwebtoken");

/**
 * Validates the Authorization: Bearer <token> header.
 * Attaches decoded payload to req.user on success.
 */
function authenticate(req, res, next) {
  const authHeader =
    req.headers["authorization"] || req.headers["Authorization"];

  if (!authHeader || !authHeader.startsWith("Bearer ")) {
    return res
      .status(401)
      .json({ error: "Missing or invalid Authorization header." });
  }

  const token = authHeader.slice(7); // strip "Bearer "

  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET);
    req.user = payload; // { sub, username, role, iat, exp }
    next();
  } catch (err) {
    return res.status(401).json({ error: "Token expired or invalid." });
  }
}

module.exports = authenticate;
