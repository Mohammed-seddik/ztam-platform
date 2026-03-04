package com.ztam.spi;

import org.keycloak.component.ComponentModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.storage.StorageId;
import org.keycloak.storage.adapter.AbstractUserAdapterFederatedStorage;

import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

/**
 * Wraps a MySQL user row into a Keycloak UserModel.
 *
 * The Keycloak-internal user ID is formatted as:
 *   f:<component-model-id>:<username>
 * which is the StorageId convention used by UserStorageProvider.
 */
public class MySqlUserAdapter extends AbstractUserAdapterFederatedStorage {

    private final String username;
    private final String role;
    private final int    dbId;   // MySQL primary key (integer)

    public MySqlUserAdapter(
            KeycloakSession session,
            RealmModel realm,
            ComponentModel storageProviderModel,
            String username,
            String role,
            int    dbId) {
        super(session, realm, storageProviderModel);
        this.username = username;
        this.role     = role;
        this.dbId     = dbId;
    }

    // ── Identity ──────────────────────────────────────────────────────────────
    @Override
    public String getId() {
        return StorageId.keycloakId(storageProviderModel, username);
    }

    @Override
    public String getUsername() {
        return username;
    }

    @Override
    public void setUsername(String username) {
        // read-only provider — no writes to the external DB
    }

    // ── Email ─────────────────────────────────────────────────────────────────
    @Override
    public String getEmail() {
        // The username IS the email in this SPI (login with email)
        return username;
    }

    @Override
    public void setEmail(String email) { /* read-only */ }

    @Override
    public boolean isEmailVerified() { return true; }

    @Override
    public void setEmailVerified(boolean verified) { /* read-only */ }

    // ── Account state ─────────────────────────────────────────────────────────
    @Override
    public boolean isEnabled() { return true; }

    @Override
    public void setEnabled(boolean enabled) { /* read-only */ }

    // ── Attributes — exposes role as a custom JWT claim ───────────────────────
    @Override
    public Map<String, List<String>> getAttributes() {
        Map<String, List<String>> attrs = super.getAttributes();
        attrs.put("role",        List.of(role));
        attrs.put("db_user_id", List.of(String.valueOf(dbId)));
        return attrs;
    }

    /**
     * Keycloak 24 uses getAttributeStream(String) instead of getAttribute(String).
     */
    @Override
    public Stream<String> getAttributeStream(String name) {
        if ("role".equals(name))        return Stream.of(role);
        if ("db_user_id".equals(name)) return Stream.of(String.valueOf(dbId));
        return super.getAttributeStream(name);
    }

    @Override
    public String getFirstAttribute(String name) {
        if ("role".equals(name))        return role;
        if ("db_user_id".equals(name)) return String.valueOf(dbId);
        return super.getFirstAttribute(name);
    }

    @Override
    public void setAttribute(String name, List<String> values) { /* read-only */ }

    @Override
    public void removeAttribute(String name) { /* read-only */ }
}
