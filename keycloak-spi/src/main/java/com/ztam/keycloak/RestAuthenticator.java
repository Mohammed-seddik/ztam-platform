package com.ztam.keycloak;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.ws.rs.core.MultivaluedMap;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.AuthenticationFlowError;
import org.keycloak.authentication.Authenticator;
import org.keycloak.events.Errors;
import org.keycloak.models.RealmModel;
import org.keycloak.models.RoleModel;
import org.keycloak.models.UserModel;
import org.keycloak.models.UserProvider;
import org.keycloak.services.messages.Messages;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

public class RestAuthenticator implements Authenticator {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Override
    public void authenticate(AuthenticationFlowContext context) {
        context.challenge(context.form().createLoginUsernamePassword());
    }

    @Override
    public void action(AuthenticationFlowContext context) {
        MultivaluedMap<String, String> formData = context.getHttpRequest().getDecodedFormParameters();
        String username = formData.getFirst("username");
        String password = formData.getFirst("password");

        if (username == null || password == null) {
            context.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                    context.form().setError(Messages.INVALID_USER).createLoginUsernamePassword());
            return;
        }

        String verifyUrl = context.getAuthenticatorConfig().getConfig().get("verify_url");
        String apiKey = context.getAuthenticatorConfig().getConfig().get("api_key");

        VerifyResponse response;
        try {
            HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
            String body = MAPPER.writeValueAsString(Map.of("username", username, "password", password));
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(verifyUrl))
                    .timeout(Duration.ofSeconds(5))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + apiKey)
                    .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                    .build();
            HttpResponse<String> httpResponse = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (httpResponse.statusCode() != 200) {
                throw new RuntimeException("verify endpoint returned status " + httpResponse.statusCode());
            }
            response = MAPPER.readValue(httpResponse.body(), VerifyResponse.class);
        } catch (Exception ex) {
            context.getEvent().error(Errors.USER_NOT_FOUND);
            context.failureChallenge(AuthenticationFlowError.INTERNAL_ERROR,
                    context.form().setError("Verification backend unavailable")
                            .createErrorPage(jakarta.ws.rs.core.Response.Status.BAD_GATEWAY));
            return;
        }

        if (!response.valid) {
            context.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                    context.form().setError(Messages.INVALID_USER).createLoginUsernamePassword());
            return;
        }

        RealmModel realm = context.getRealm();
        UserProvider users = context.getSession().users();

        UserModel user = users.getUserByUsername(realm, username);
        if (user == null) {
            user = users.addUser(realm, username);
            user.setEnabled(true);
        }

        if (response.email != null) user.setEmail(response.email);
        if (response.name != null) user.setFirstName(response.name);
        if (response.userId != null) user.setSingleAttribute("db_user_id", String.valueOf(response.userId));

        if (response.roles != null) {
            for (String roleName : response.roles) {
                RoleModel role = realm.getRole(roleName);
                if (role != null && !user.hasRole(role)) {
                    user.grantRole(role);
                }
            }
        }

        context.setUser(user);
        context.success();
    }

    @Override
    public boolean requiresUser() {
        return false;
    }

    @Override
    public boolean configuredFor(org.keycloak.models.KeycloakSession session, RealmModel realm, UserModel user) {
        return true;
    }

    @Override
    public void setRequiredActions(org.keycloak.models.KeycloakSession session, RealmModel realm, UserModel user) {}

    @Override
    public void close() {}
}
