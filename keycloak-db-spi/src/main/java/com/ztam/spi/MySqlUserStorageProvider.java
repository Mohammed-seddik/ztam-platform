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
 * ZTAM Keycloak User Storage SPI — Keycloak 26.
 * SELECT-only against TestApp's MySQL database.
 * Implements UserLookupProvider + CredentialInputValidator.
 */
public class MySqlUserStorageProvider
        implements UserStorageProvider, UserLookupProvider, CredentialInputValidator {

    private static final Logger LOG =
            Logger.getLogger(MySqlUserStorageProvider.class.getName());

    private final KeycloakSession session;
    private final ComponentModel  model;

    private final String dbType;
    private final String jdbcUrl;
    private final String dbUser;
    private final String dbPass;
    private final String tableName;
    private final String usernameCol;
    private final String passwordCol;
    private final String roleCol;

    public MySqlUserStorageProvider(KeycloakSession session, ComponentModel model) {
        this.session = session;
        this.model   = model;

        this.dbType      = cfg(MySqlUserStorageProviderFactory.CFG_TYPE);
        LOG.log(Level.INFO, "[ZTAM SPI] Database type: {0}", dbType);
        String host = cfg(MySqlUserStorageProviderFactory.CFG_HOST);
        String port = cfg(MySqlUserStorageProviderFactory.CFG_PORT);
        String name = cfg(MySqlUserStorageProviderFactory.CFG_NAME);

        if ("postgresql".equalsIgnoreCase(dbType)) {
            this.jdbcUrl = "jdbc:postgresql://" + host + ":" + port + "/" + name + "?ssl=false";
        } else {
            this.jdbcUrl = "jdbc:mysql://" + host + ":" + port + "/" + name
                         + "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC";
        }

        this.dbUser      = cfg(MySqlUserStorageProviderFactory.CFG_USER);
        this.dbPass      = cfg(MySqlUserStorageProviderFactory.CFG_PASS);
        this.tableName   = cfg(MySqlUserStorageProviderFactory.CFG_TABLE);
        this.usernameCol = cfg(MySqlUserStorageProviderFactory.CFG_COL_USER);
        this.passwordCol = cfg(MySqlUserStorageProviderFactory.CFG_COL_PASS);
        this.roleCol     = cfg(MySqlUserStorageProviderFactory.CFG_COL_ROLE);
    }

    private String cfg(String key) {
        String value = model.getConfig().getFirst(key);
        return value != null ? value : "";
    }

    @Override
    public void close() {}

    // ─── User Lookup ────────────────────────────────────────────────────────
    @Override
    public UserModel getUserByUsername(RealmModel realm, String username) {
        LOG.log(Level.INFO, "[ZTAM SPI] getUserByUsername: {0}", username);
        return findUser(realm, username);
    }

    @Override
    public UserModel getUserByEmail(RealmModel realm, String email) {
        LOG.log(Level.INFO, "[ZTAM SPI] getUserByEmail: {0}", email);
        return findUser(realm, email);
    }

    @Override
    public UserModel getUserById(RealmModel realm, String id) {
        // id format: "f:<component-id>:<loginId>"
        if (id == null || !id.startsWith("f:")) return null;
        String[] parts = id.split(":", 3);
        if (parts.length < 3) return null;
        return findUser(realm, parts[2]);
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

        LOG.log(Level.INFO, "[ZTAM SPI] isValid called for: {0}", loginId);

        String storedHash = fetchPasswordHash(loginId);
        if (storedHash == null) {
            LOG.log(Level.WARNING, "[ZTAM SPI] No password hash found for: {0}", loginId);
            return false;
        }

        boolean valid = verifyBcrypt(supplied, storedHash);
        LOG.log(Level.INFO, "[ZTAM SPI] Password check for {0}: {1}",
                new Object[]{loginId, valid ? "OK" : "FAIL"});
        return valid;
    }

    // ─── Internal helpers ────────────────────────────────────────────────────

    /**
     * Single SQL query: fetches id, usernameCol, and roleCol in one round trip.
     */
    private UserModel findUser(RealmModel realm, String loginId) {
        if (loginId == null || loginId.isBlank()) return null;

        String sql = "SELECT `id`, " + esc(usernameCol) + ", " + esc(roleCol)
                   + " FROM " + esc(tableName)
                   + " WHERE " + esc(usernameCol) + " = ? LIMIT 1";

        try (Connection conn = openConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {

            ps.setString(1, loginId);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    int    dbId    = rs.getInt("id");
                    String uname   = rs.getString(usernameCol);
                    String rawRole = rs.getString(roleCol);
                    String role    = normalizeRole(rawRole);

                    LOG.log(Level.INFO, "[ZTAM SPI] Found user: {0} role: {1}",
                            new Object[]{uname, role});
                    return new MySqlUserAdapter(session, realm, model, uname, role, dbId);
                }
            }
        } catch (SQLException e) {
            LOG.log(Level.SEVERE, "[ZTAM SPI] DB error during user lookup: " + e.getMessage(), e);
        }
        return null;
    }

    /**
     * Fetch only the bcrypt hash for credential validation.
     */
    private String fetchPasswordHash(String loginId) {
        String sql = "SELECT " + esc(passwordCol)
                   + " FROM " + esc(tableName)
                   + " WHERE " + esc(usernameCol) + " = ? LIMIT 1";

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
     * bcrypt-only password verification.
     * Normalizes $2b$ (Node.js/bcryptjs) -> $2a$ (jBCrypt) — mathematically identical.
     * SHA-256 and MD5 are NOT supported — they are not password hashing functions.
     */
    private boolean verifyBcrypt(String plain, String stored) {
        try {
            String hash = stored.startsWith("$2b$")
                    ? "$2a$" + stored.substring(4)
                    : stored;
            return org.mindrot.jbcrypt.BCrypt.checkpw(plain, hash);
        } catch (Exception e) {
            LOG.log(Level.SEVERE, "[ZTAM SPI] bcrypt verification error: " + e.getMessage(), e);
            return false;
        }
    }

    /**
     * Normalize DB role values to the canonical ZTAM role set.
     * "user" stays "user" — it is a valid ZTAM role defined in permissions.json.
     */
    static String normalizeRole(String raw) {
        if (raw == null) return "viewer";
        return switch (raw.toLowerCase().trim()) {
            case "admin", "superuser", "manager", "super_admin", "administrator" -> "admin";
            case "editor"                                                         -> "editor";
            case "user", "member", "contributor"                                 -> "user";
            default                                                               -> "viewer";
        };
    }

    /** Wrap identifier in backticks (MySQL) or double quotes (PostgreSQL) to prevent SQL identifier injection. */
    private String esc(String id) {
        if ("postgresql".equalsIgnoreCase(dbType)) {
            return "\"" + id.replace("\"", "") + "\"";
        }
        return "`" + id.replace("`", "") + "`";
    }
}
