package com.ztam.keycloak;

import java.util.List;

public class VerifyResponse {
    public boolean valid;
    public Long userId;
    public String email;
    public String name;
    public List<String> roles;
}
