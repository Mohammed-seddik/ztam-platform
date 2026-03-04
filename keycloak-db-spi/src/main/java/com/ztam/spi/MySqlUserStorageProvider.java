package com.ztam.spi;

import org.keycloak.component.ComponentModel;
import org.keycloak.credential.CredentialInput;
import org.keycloak.credential.CredentialInputValidator;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.models.credential.PasswordCredentialModel;
import org.keycloak.storage.UserStorageProvider;
import org.keycloak.storage.user.UserLookupProvider;

import java.sql.*;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * The core SPI implementation.
 * Implements:
 *  - UserLookupProvider   → getUserByUsername / getUserByEmail / getUserById
 *  - CredentialInputValidator → validates the supplied password against MySQL
 *
 * This class NEVER issues INSERT/UPDATE/DELETE — SELECT only.
 */
public class MySqlUserStorageProvider
        implements UserStorageProvider, UserLookupProvider, CredentialInputValidator {

    private static final Logger LOG =
            Logger.getLogger(MySqlUserStorageProvider.class.getName());

    private final KeycloakSession session;
    private final ComponentModel  model;

    // Resolved config values
    private final String jdbcUrl;
    private final String dbUser;
    private final String dbPass;
    private final String tableName;
    private final String usernameCol;
    private final String passwordCol;
    private final String roleCol;
    private final String hashAlgo;

    public MySqlUserStorageProvider(KeycloakSession session, ComponentModel model) {
        this.session = session;
        this.model   = model;

        String host = cfg(MySqlUserStorageProviderFactory.CFG_HOST);
        String port = cfg(MySqlUserStorageProviderFactory.CFG_PORT);
        String name = cfg(MySqlUserStorageProviderFactory.CFG_NAME);

        this.jdbcUrl     = "jdbc:mysql://" + host + ":" + port + "/" + name
                         + "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC";
        this.dbUser      = cfg(MySqlUserStorageProviderFactory.CFG_USER);
        this.dbPass      = cfg(MySqlUserStorageProviderFactory.CFG_PASS);
        this.tableName   = cfg(MySqlUserStorageProviderFactory.CFG_TABLE);
        this.usernameCol = cfg(MySqlUserStorageProviderFactory.CFG_COL_USER);
        this.passwordCol = cfg(MySqlUserStorageProviderFactory.CFG_COL_PASS);
        this.roleCol     = cfg(MySqlUserStorageProviderFactory.CFG_COL_ROLE);
        this.hashAlgo    = cfg(MySqlUserStorageProviderFactory.CFG_HASH);
    }

    private String cfg(String key) {
        String value = model.getConfig().getFirst(key);
        return value != null ? value : "";
    }

    // ─── Lifecycle ──────────────────────────────────────────────────────────
    @Override
    public void close() {
        // Connections are opened per-request; nothing to close here.
    }

    // ─── User Lookup ────────────────────────────────────────────────────────
    @Override
    public UserModel getUserByUsername(RealmModel realm, String username) {
        LOG.log(java.util.logging.Level.INFO, "[ZTAM SPI] getUserByUsername: {0}", username);
        return findUserByLoginId(realm, username);
    }

    @Override
    public UserModel getUserByEmail(RealmModel realm, String email) {
        LOG.log(java.util.logging.Level.INFO, "[ZTAM SPI] getUserByEmail: {0}", email);
        return findUserByLoginId(realm, email);
    }

    @Override
    public UserModel getUserById(RealmModel realm, String id) {
        // id format produced by MySqlUserAdapter.getId() → "f:<component-id>:<loginId>"
        String[] parts = id.split(":", 3);
        if (parts.length < 3) return null;
        return findUserByLoginId(realm, parts[2]);
    }

    // ─── Credential Validation ───────────────────────────────────────────────
    @Override
    public boolean supportsCredentialType(String credentialType) {
        return PasswordCredentialModel.TYPE.equals(credentialType);
    }

    @Override
    public boolean isConfiguredFor(RealmModel realm, UserModel user, String credentialType) {
        return supportsCredentialType(credentialType);
    }

    @Override
    public boolean isValid(RealmModel realm, UserModel user, CredentialInput input) {
        if (!supportsCredentialType(input.getType())) return false;

        String supplied = input.getChallengeResponse();
        String loginId  = user.getUsername();

        LOG.log(java.util.logging.Level.INFO, "[ZTAM SPI] isValid called for: {0}", loginId);

        String storedHash = fetchPasswordHash(loginId);
        if (storedHash == null) {
            LOG.log(java.util.logging.Level.WARNING, "[ZTAM SPI] No password hash found for: {0}", loginId);
            return false;
        }

        boolean valid = verifyPassword(supplied, storedHash);
        LOG.log(java.util.logging.Level.INFO, "[ZTAM SPI] Password check for {0}: {1}", new Object[]{loginId, valid ? "OK" : "FAIL"});
        return valid;
    }

    // ─── Internal helpers ────────────────────────────────────────────────────

    /**
     * Look up a user row by the configured username/email column.
     * Returns null if not found or on any DB error.
     */
    private UserModel findUserByLoginId(RealmModel realm, String loginId) {
        // Sanitise: loginId must not contain SQL metacharacters (we still use PreparedStatement)
        if (loginId == null || loginId.isBlank()) return null;

        String sql = "SELECT id, " + escape(usernameCol) + ", "
                   + escape(roleCol)
                   + " FROM " + escape(tableName)
                   + " WHERE " + escape(usernameCol) + " = ? LIMIT 1";

        try (Connection conn = openConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setString(1, loginId);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    int    dbId    = rs.getInt("id");
                    String username = rs.getString(usernameCol);
                    String rawRole  = rs.getString(roleCol);
                    String role     = normalizeRole(rawRole);

                    LOG.log(java.util.logging.Level.INFO, "[ZTAM SPI] Found user: {0} role: {1}", new Object[]{username, role});
                    return new MySqlUserAdapter(session, realm, model, username, role, dbId);
                }
            }
        } catch (SQLException e) {
            LOG.log(Level.SEVERE, "[ZTAM SPI] DB error during user lookup: " + e.getMessage(), e);
        }
        return null;
    }

    /**
     * Fetch only the password hash for a given login identifier.
     */
    private String fetchPasswordHash(String loginId) {
        String sql = "SELECT " + escape(passwordCol)
                   + " FROM " + escape(tableName)
                   + " WHERE " + escape(usernameCol) + " = ? LIMIT 1";

        try (Connection conn = openConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setString(1, loginId);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) return rs.getString(passwordCol);
            }
        } catch (SQLException e) {
            LOG.log(Level.SEVERE, "[ZTAM SPI] DB error fetching hash: " + e.getMessage(), e);
        }
        return null;
    }

    private Connection openConnection() throws SQLException {
        return DriverManager.getConnection(jdbcUrl, dbUser, dbPass);
    }

    /**
     * Verify a plaintext password against a stored hash using the configured algorithm.
     */
    private boolean verifyPassword(String plain, String stored) {
        try {
            return switch (hashAlgo.toLowerCase()) {
                // Node.js bcrypt uses $2b$ prefix; jbcrypt only understands $2a$.
                // They are mathematically identical — safe to normalize.
                case "bcrypt" -> org.mindrot.jbcrypt.BCrypt.checkpw(plain,
                        stored.startsWith("$2b$") ? "$2a$" + stored.substring(4) : stored);
                case "sha256" -> hashSha256(plain).equalsIgnoreCase(stored);
                case "md5"    -> hashMd5(plain).equalsIgnoreCase(stored);
                default -> {
                    LOG.log(java.util.logging.Level.WARNING, "[ZTAM SPI] Unknown hash algorithm: {0}", hashAlgo);
                    yield false;
                }
            };
        } catch (Exception e) {
            LOG.log(Level.SEVERE, "[ZTAM SPI] Password verification error: " + e.getMessage(), e);
            return false;
        }
    }

    private String hashSha256(String input) throws Exception {
        java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
        byte[] digest = md.digest(input.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        return bytesToHex(digest);
    }

    private String hashMd5(String input) throws Exception {
        java.security.MessageDigest md = java.security.MessageDigest.getInstance("MD5");
        byte[] digest = md.digest(input.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        return bytesToHex(digest);
    }

    private static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    /**
     * Normalize raw DB role values to admin | editor | viewer.
     */
    static String normalizeRole(String raw) {
        if (raw == null) return "viewer";
        return switch (raw.toLowerCase().trim()) {
            case "admin", "superuser", "manager", "super_admin", "administrator" -> "admin";
            case "editor", "user", "member", "contributor"                       -> "editor";
            default                                                               -> "viewer";
        };
    }

    /**
     * Escape a column/table identifier by wrapping in backticks
     * and stripping any pre-existing backticks — simple protection
     * against accidental injection via config values.
     */
    private static String escape(String identifier) {
        return "`" + identifier.replace("`", "") + "`";
    }
}
