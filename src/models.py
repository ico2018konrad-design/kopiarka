"""Modele danych."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Practitioner:
    """Reprezentuje profil praktyka z Heallist."""

    profile_url: str
    name: Optional[str] = None
    specializations: list[str] = field(default_factory=list)
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    social_links: dict = field(default_factory=dict)
    city: Optional[str] = None
    country: Optional[str] = None
    address: Optional[str] = None
    bio: Optional[str] = None
    raw_json: Optional[str] = None
