package com.ztam.spi;

import java.util.List;
import java.util.logging.Logger;

import org.keycloak.component.ComponentModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.provider.ProviderConfigurationBuilder;
import org.keycloak.storage.UserStorageProviderFactory;

/**
 * Keycloak 26 User Storage SPI Factory — registered as "mysql-db-provider".
 * Appears in the Keycloak Admin UI under User Federation as "MySQL DB Provider".
 */
public class MySqlUserStorageProviderFactory
        implements UserStorageProviderFactory<MySqlUserStorageProvider> {

    private static final Logger LOG =
            Logger.getLogger(MySqlUserStorageProviderFactory.class.getName());

    public static final String PROVIDER_ID = "mysql-db-provider";

    // ── Configuration field keys ────────────────────────────────────────────
    static final String CFG_TYPE      = "db_type";
    static final String CFG_HOST      = "db_host";
    static final String CFG_PORT      = "db_port";
    static final String CFG_NAME      = "db_name";
    static final String CFG_USER      = "db_user";
    static final String CFG_PASS      = "db_pass";
    static final String CFG_TABLE     = "table_name";
    static final String CFG_COL_USER  = "username_col";
    static final String CFG_COL_PASS  = "password_col";
    static final String CFG_COL_ROLE  = "role_col";
    static final String CFG_HASH      = "hash_algorithm";

    // ── Metadata ────────────────────────────────────────────────────────────
    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public String getHelpText() {
        return "Authenticates users against a client-owned MySQL or PostgreSQL database (read-only).";
    }

    // ── Configuration properties (visible in Keycloak Admin UI) ─────────────
    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return ProviderConfigurationBuilder.create()
                .property()
                    .name(CFG_TYPE).label("Database Type")
                    .helpText("Choose the client's database engine.")
                    .type(ProviderConfigProperty.LIST_TYPE)
                    .options("mysql", "postgresql")
                    .defaultValue("mysql")
                    .add()
                .property()
                    .name(CFG_HOST).label("DB Host")
                    .helpText("MySQL server hostname or IP (e.g. 192.168.1.10)")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .required(true)
                    .add()
                .property()
                    .name(CFG_PORT).label("DB Port")
                    .helpText("MySQL port (default: 3306)")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("3306")
                    .add()
                .property()
                    .name(CFG_NAME).label("DB Name")
                    .helpText("Name of the MySQL database")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .required(true)
                    .add()
                .property()
                    .name(CFG_USER).label("DB Username")
                    .helpText("MySQL user with SELECT privilege")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .required(true)
                    .add()
                .property()
                    .name(CFG_PASS).label("DB Password")
                    .helpText("MySQL user password")
                    .type(ProviderConfigProperty.PASSWORD)
                    .secret(true)
                    .add()
                .property()
                    .name(CFG_TABLE).label("Users Table")
                    .helpText("Table that stores user accounts (e.g. users)")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .required(true)
                    .add()
                .property()
                    .name(CFG_COL_USER).label("Username / Email Column")
                    .helpText("Column used as the login identifier (e.g. email)")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("email")
                    .add()
                .property()
                    .name(CFG_COL_PASS).label("Password Hash Column")
                    .helpText("Column that stores the hashed password (e.g. password)")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("password")
                    .add()
                .property()
                    .name(CFG_COL_ROLE).label("Role Column")
                    .helpText("Column that stores the user role (e.g. role)")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("role")
                    .add()
                .property()
                    .name(CFG_HASH).label("Hash Algorithm")
                    .helpText("Password hashing algorithm. Only bcrypt is supported (secure key-stretching).")
                    .type(ProviderConfigProperty.LIST_TYPE)
                    .options("bcrypt")
                    .defaultValue("bcrypt")
                    .add()
                .build();
    }

    // ── Factory method ───────────────────────────────────────────────────────
    @Override
    public MySqlUserStorageProvider create(KeycloakSession session, ComponentModel model) {
        LOG.log(java.util.logging.Level.INFO, "[ZTAM SPI] Creating MySqlUserStorageProvider for component: {0}", model.getName());
        return new MySqlUserStorageProvider(session, model);
    }
}
