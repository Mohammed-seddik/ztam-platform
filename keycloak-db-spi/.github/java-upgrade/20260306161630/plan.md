# Upgrade Plan: keycloak-db-spi (20260306161630)

- **Generated**: 2026-03-06 16:16:30
- **HEAD Branch**: master
- **HEAD Commit ID**: 019f42cbc655aa9e64cfa63a8be85e9b7a7f910a

## Available Tools

**JDKs**

- JDK 17.0.18: /usr/lib/jvm/java-17-openjdk-amd64/bin (current project JDK, used by step 2)
- JDK 21.0.10: /usr/lib/jvm/java-21-openjdk-amd64/bin (target version, used by steps 3-4)

**Build Tools**

- Maven: **<TO_BE_INSTALLED>** (required by all build steps)

## Guidelines

None specified by user.

> Note: You can add any specific guidelines or constraints for the upgrade process here if needed, bullet points are preferred.

## Options

- Working branch: appmod/java-upgrade-20260306161630
- Run tests before and after the upgrade: true

## Upgrade Goals

- Upgrade Java from 17 to 21 (LTS)

### Technology Stack

<!--
  Table of core dependencies and their compatibility with upgrade goals.
  IMPORTANT: Analyze ALL modules in multi-module projects, not just the root module.
  Only include: direct dependencies + those critical for upgrade compatibility.
  CRITICAL: Identify and clearly flag EOL (End of Life) dependencies - these pose security risks and must be upgraded.

  Columns:
  - Technology/Dependency: Name of the dependency (mark EOL dependencies with "⚠️ EOL" suffix)
  - Current: Version currently in use
  - Min Compatible Version: Minimum version that works with upgrade goals (or N/A if replaced)
  - Why Incompatible: Explanation of incompatibility, or "-" if already compatible. For EOL deps, mention security/support concerns.

  SAMPLE:
  | Technology/Dependency    | Current | Min Compatible | Why Incompatible                               |
  | ------------------------ | ------- | -------------- | ---------------------------------------------- |
  | Java                     | 8       | 21             | User requested                                 |
  | Spring Boot              | 2.5.0   | 3.2.0          | User requested                                 |
  | Spring Framework         | 5.3.x   | 6.1.x          | Spring Boot 3.2 requires Spring Framework 6.1+ |
  | Hibernate                | 5.4.x   | 6.1.x          | Spring Boot 3.x requires Hibernate 6+          |
  | javax.servlet ⚠️ EOL     | 4.0     | N/A            | Replaced by jakarta.servlet in Spring Boot 3.x; javax namespace EOL |
  | Log4j ⚠️ EOL             | 1.2.17  | N/A            | EOL since 2015, critical security vulnerabilities; replace with Logback/Log4j2 |
  | DWR ⚠️ EOL             | 3.0.1.rc  | N/A            | EOL since 2017, no longer maintained; consider replacing with modern alternatives |
  | Lombok                   | 1.18.20 | 1.18.20        | -                                              |
-->

| Technology/Dependency | Current | Min Compatible | Why Incompatible |
| --------------------- | ------- | -------------- | ---------------- |
| Java                  | 17      | 21             | User requested   |
| Keycloak SPI          | 26.5.5  | 26.5.5         | -                |
| MySQL Connector/J     | 8.3.0   | 8.3.0          | -                |
| JBcrypt               | 0.4     | 0.4            | -                |
| Jakarta WS.RS API     | 3.1.0   | 3.1.0          | -                |
| Maven Shade Plugin    | 3.5.2   | 3.5.2          | -                |

### Derived Upgrades

None required. All dependencies are already compatible with Java 21.

## Upgrade Steps

- **Step 1: Setup Environment**
  - **Rationale**: Install Maven build tool identified as missing in "Available Tools" section. JDK 17 and JDK 21 are already available.
  - **Changes to Make**:
    - [ ] Install Maven using system package manager (apt)
    - [ ] Verify Maven installation
    - [ ] Update Available Tools table with Maven path and version
  - **Verification**:
    - Command: `mvn -version`
    - Expected: Maven successfully installed and available in PATH

---

- **Step 2: Setup Baseline**
  - **Rationale**: Establish pre-upgrade compile and test results to measure upgrade success against.
  - **Changes to Make**:
    - [ ] Run baseline compilation with JDK 17
    - [ ] Run baseline tests with JDK 17 (if tests exist)
    - [ ] Document compilation and test results
  - **Verification**:
    - Command: `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 mvn clean test-compile && mvn clean test`
    - JDK: /usr/lib/jvm/java-17-openjdk-amd64
    - Expected: Document SUCCESS/FAILURE, test pass rate (forms acceptance criteria)

---

- **Step 3: Upgrade Java Version in POM**
  - **Rationale**: Update Maven configuration to target Java 21.
  - **Changes to Make**:
    - [ ] Update `<java.version>17</java.version>` → `21`
    - [ ] Update `<maven.compiler.source>17</maven.compiler.source>` → `21`
    - [ ] Update `<maven.compiler.target>17</maven.compiler.target>` → `21`
  - **Verification**:
    - Command: `JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 mvn clean test-compile`
    - JDK: /usr/lib/jvm/java-21-openjdk-amd64
    - Expected: Compilation SUCCESS (both main and test code)

---

- **Step 4: Final Validation**
  - **Rationale**: Verify all upgrade goals met, project compiles successfully, all tests pass (if tests enabled in Options).
  - **Changes to Make**:
    - [ ] Verify all target versions in pom.xml (Java 21)
    - [ ] Resolve ALL TODOs and temporary workarounds from previous steps
    - [ ] Clean rebuild with target JDK 21
    - [ ] Fix any remaining compilation errors
    - [ ] Run full test suite and fix ALL test failures (iterative fix loop until 100% pass)
    - [ ] Build final JAR with `mvn clean package`
  - **Verification**:
    - Command: `JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 mvn clean test && mvn clean package`
    - JDK: /usr/lib/jvm/java-21-openjdk-amd64
    - Expected: Compilation SUCCESS + 100% tests pass (or ≥ baseline) + JAR successfully built

## Key Challenges

- **Keycloak Runtime Compatibility**
  - **Challenge**: This SPI runs inside Keycloak server 26.5.5. Need to verify Keycloak container runtime supports Java 21.
  - **Strategy**: Keycloak 26.x officially supports Java 21. The SPI is compiled and packaged separately, then deployed to Keycloak's providers directory. The compiled JAR will work as long as it doesn't use Java 21-specific APIs not available in Keycloak's runtime.

- **BCrypt Library Age**
  - **Challenge**: JBcrypt 0.4 is an older library (last updated 2014). Need to verify it works correctly with Java 21's security providers.
  - **Strategy**: Test password verification after upgrade. If issues arise, consider upgrading to a more recent bcrypt implementation or using Spring Security's BCryptPasswordEncoder.

- **MySQL JDBC Driver**
  - **Challenge**: MySQL Connector/J 8.3.0 needs to work with Java 21's updated security and TLS implementation.
  - **Strategy**: Test database connectivity after upgrade. Version 8.3.0 is recent (2024) and should support Java 21, but verify SSL/TLS connections work correctly.
