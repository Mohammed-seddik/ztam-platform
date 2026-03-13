FROM maven:3.9-eclipse-temurin-17 AS builder
WORKDIR /build
COPY pom.xml .
COPY src ./src
RUN mvn -q -DskipTests package

FROM quay.io/keycloak/keycloak:26.0.7
COPY --from=builder /build/target/keycloak-rest-authenticator-1.0.0.jar /opt/keycloak/providers/
RUN /opt/keycloak/bin/kc.sh build
