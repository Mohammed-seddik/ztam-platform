package com.ztam.keycloak;

import org.keycloak.Config;
import org.keycloak.authentication.Authenticator;
import org.keycloak.authentication.AuthenticatorFactory;
import org.keycloak.models.AuthenticationExecutionModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;

import java.util.List;

public class RestAuthenticatorFactory implements AuthenticatorFactory {
    public static final String PROVIDER_ID = "rest-authenticator";

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public String getDisplayType() {
        return "REST Legacy User Verifier";
    }

    @Override
    public String getHelpText() {
        return "Validates username/password by calling the client app /auth/verify endpoint.";
    }

    @Override
    public Authenticator create(KeycloakSession session) {
        return new RestAuthenticator();
    }

    @Override
    public boolean isConfigurable() {
        return true;
    }

    @Override
    public AuthenticationExecutionModel.Requirement[] getRequirementChoices() {
        return new AuthenticationExecutionModel.Requirement[]{
                AuthenticationExecutionModel.Requirement.REQUIRED,
                AuthenticationExecutionModel.Requirement.DISABLED
        };
    }

    @Override
    public boolean isUserSetupAllowed() {
        return false;
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        ProviderConfigProperty verifyUrl = new ProviderConfigProperty();
        verifyUrl.setName("verify_url");
        verifyUrl.setLabel("Verify URL");
        verifyUrl.setHelpText("URL for POST /auth/verify");
        verifyUrl.setType(ProviderConfigProperty.STRING_TYPE);

        ProviderConfigProperty apiKey = new ProviderConfigProperty();
        apiKey.setName("api_key");
        apiKey.setLabel("API Key");
        apiKey.setHelpText("Bearer token sent to /auth/verify");
        apiKey.setType(ProviderConfigProperty.PASSWORD);

        return List.of(verifyUrl, apiKey);
    }

    @Override
    public void init(Config.Scope config) {}

    @Override
    public void postInit(KeycloakSessionFactory factory) {}

    @Override
    public void close() {}

    @Override
    public String getReferenceCategory() {
        return null;
    }
}
