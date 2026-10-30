"""Trusted skill-bundle installer — validates a bundle authored in an agent's
scratch dir and installs it atomically into the Kiln project's skills/ tree."""

from kiln_harness_tuner.skill_installer.bundle import install_bundle, sweep_staging

__all__ = ["install_bundle", "sweep_staging"]
