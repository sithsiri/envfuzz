# Release verification

## Purpose

Verify that documented EnvFuzz runtime profiles and releases can be installed and exercised reproducibly on supported platforms.

## ADDED Requirements

### Requirement: Automated CPU verification
CI SHALL run lint, unit and property tests, a bounded headless integration check, and scheduled regression and performance runs. It SHALL retain diagnostic artifacts and report configured coverage and performance gates.

#### Scenario: A required fixture fails
- **WHEN** a required CPU integration or replay check falls outside its declared tolerance
- **THEN** CI fails the check and preserves the result bundle and logs

### Requirement: Traceable multi-architecture releases
A release SHALL publish versioned amd64 and arm64 images with a component manifest and release notes after the matching smoke checks succeed.

#### Scenario: A supported image is released
- **WHEN** the tagged image is pulled for a supported architecture
- **THEN** the documented smoke run completes and the version manifest identifies the released build

### Requirement: Verified participant runtime profiles
The project SHALL document and verify CPU-only workflows for Apple Silicon and Windows WSL2 and provide a low-memory profile with measured resource and fidelity limits.

#### Scenario: A low-memory host runs the smoke fixture
- **WHEN** the documented reduced-resource profile is selected
- **THEN** the smoke result identifies the profile and the guide states the measured limits and affected observations
