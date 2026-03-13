import hashlib
import json
import logging
import os
import secrets
from urllib.parse import urlencode

import jwt
import mysql.connector
import requests
from flask import Flask, jsonify, redirect, render_template, request, session

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "dev-secret")

VERIFY_API_KEY = os.getenv("VERIFY_API_KEY", "demo-shared-api-key")
OIDC_ISSUER_INTERNAL = os.getenv("OIDC_ISSUER", "http://keycloak:8080/realms/demo-client")
OIDC_ISSUER_PUBLIC = os.getenv("OIDC_PUBLIC_ISSUER", "http://localhost:8080/realms/demo-client")
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "client-demo-app")
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "client-demo-secret")
OIDC_REDIRECT_URI = os.getenv("OIDC_REDIRECT_URI", "https://localhost/oidc/callback")


def db_conn():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "mysql"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "appuser"),
        password=os.getenv("MYSQL_PASSWORD", "apppass"),
        database=os.getenv("MYSQL_DB", "client_app"),
    )


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    candidate = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return secrets.compare_digest(candidate, expected_hash)


@app.get("/")
def home():
    user = session.get("user")
    if not user:
        return redirect("/login")
    return render_template("home.html", user=user)


@app.get("/login")
def login_page():
    return render_template("login.html")


@app.get("/oidc/login")
def oidc_login():
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    session["oidc_state"] = state
    session["oidc_nonce"] = nonce

    params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": OIDC_REDIRECT_URI,
        "state": state,
        "nonce": nonce,
    }
    return redirect(f"{OIDC_ISSUER_PUBLIC}/protocol/openid-connect/auth?{urlencode(params)}")


@app.get("/oidc/callback")
def oidc_callback():
    if request.args.get("state") != session.get("oidc_state"):
        return "invalid state", 400

    code = request.args.get("code")
    token_res = requests.post(
        f"{OIDC_ISSUER_INTERNAL}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": OIDC_CLIENT_ID,
            "client_secret": OIDC_CLIENT_SECRET,
            "code": code,
            "redirect_uri": OIDC_REDIRECT_URI,
        },
        timeout=5,
    )
    token_res.raise_for_status()
    tokens = token_res.json()
    id_token = tokens["id_token"]

    claims = jwt.decode(id_token, options={"verify_signature": False, "verify_aud": False})
    session["user"] = {
        "sub": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "db_user_id": claims.get("db_user_id"),
        "roles": claims.get("realm_access", {}).get("roles", []),
    }
    return redirect("/")


@app.post("/auth/verify")
def auth_verify():
    authz = request.headers.get("Authorization", "")
    if authz != f"Bearer {VERIFY_API_KEY}":
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    username = payload.get("username", "")
    password = payload.get("password", "")

    logging.info("/auth/verify called username=%s remote=%s", username, request.remote_addr)

    conn = db_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, username, email, full_name, role, password_salt, password_hash FROM users WHERE username=%s",
        (username,),
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"valid": False})

    if not verify_password(password, user["password_salt"], user["password_hash"]):
        return jsonify({"valid": False})

    return jsonify(
        {
            "valid": True,
            "userId": user["id"],
            "email": user["email"],
            "name": user["full_name"],
            "roles": [user["role"]],
        }
    )


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")))
