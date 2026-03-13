CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  email VARCHAR(255) NOT NULL,
  full_name VARCHAR(255) NOT NULL,
  role VARCHAR(32) NOT NULL,
  password_salt VARCHAR(64) NOT NULL,
  password_hash CHAR(64) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO users (username, email, full_name, role, password_salt, password_hash) VALUES
  ('alice', 'alice@example.local', 'Alice', 'admin', 'alice_demo_salt', SHA2(CONCAT('alice_demo_salt', 'password123'), 256)),
  ('bob', 'bob@example.local', 'Bob', 'user', 'bob_demo_salt', SHA2(CONCAT('bob_demo_salt', 'password123'), 256));
