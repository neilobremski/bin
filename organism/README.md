# Synthetic Organism Biology: A Technical Field Manual

## Table of Contents

- [Overview: The Celluar Architecture](#overview-the-celluar-architecture)
- [Chapter 1: Layer 0 - Metabolism (The Spark)](#chapter-1-layer-0---metabolism-the-spark)
- [Chapter 2: Layer 1 - Nervous System (The Ganglion)](#chapter-2-layer-1---nervous-system-the-ganglion)
- [Chapter 3: Layer 2 - Circulatory System (The Artery)](#chapter-3-layer-2---circulatory-system-the-artery)
- [Chapter 4: The CLI Contract (Interfacing)](#chapter-4-the-cli-contract-interfacing)
- [Chapter 5: Organ Anatomy (Implementation Guide)](#chapter-5-organ-anatomy-implementation-guide)
- [Chapter 6: Local Lab (Mocking and Testing)](#chapter-6-local-lab-mocking-and-testing)

## Overview: The Celluar Architecture

The **Synthetic Organism** is a bio-mimetic framework for distributed computing in restricted, ephemeral environments such as cloud shells and firewalled containers. Unlike traditional service-oriented architectures that rely on static IP discovery and persistent uptime, this system treats computation as a metabolic process.

The architecture is split into three decoupled planes:

- **Metabolism (Layer 0)**: Managed by the Spark. It utilizes a time-based cycle and `flock`-gated execution to ensure "dormant" organs consume zero resources when idle. It enforces a Cadence—a configurable frequency for organ execution—while allowing for immediate "excitation" via external stimulus.

- **Nervous System (Layer 1)**: The control plane. It uses MQTT as a "Spinal Cord" for long-range signaling. Each body part (container) hosts a Ganglion sidecar that maintains persistent connections, routes signals to local organs via file-system drops (`.stimulus/`), and manages the metabolic wake-up of dormant local processes.

- **Circulatory System (Layer 2)**: The data plane. It handles high-bandwidth payloads using NATS as a "Heart" relay. Data is moved in Blobs, identified by SHA-256 hashes. The Artery sidecar handles chunking, content-addressing, and local caching. Data is ephemeral by design, utilizing short TTL (Time-To-Live) to prevent system congestion.

By decoupling signaling (Nervous) from heavy data transfer (Circulatory) and anchoring both in a metabolic lifecycle (Spark), the organism bypasses container isolation and firewall restrictions without requiring complex network configuration.

## Chapter 1: Layer 0 - Metabolism (The Spark)

## Chapter 2: Layer 1 - Nervous System (The Ganglion)

## Chapter 3: Layer 2 - Circulatory System (The Artery)

## Chapter 4: The CLI Contract (Interfacing)

## Chapter 5: Organ Anatomy (Implementation Guide)

## Chapter 6: Local Lab (Mocking and Testing)
