"""Sanitized errors at data and command boundaries."""

from __future__ import annotations




class ContractError(ValueError):
    """Safe, value-free error suitable for the command-line boundary."""
