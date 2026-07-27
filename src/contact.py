"""
contact.py

Step 1: What a "contact" looks like in our system.

This is the shape of a single lead/contact, holding just the fields we need
to score them and route them to the right salesperson. Later this will get
populated from a HubSpot CSV export (and eventually a live API pull) — for
now we'll just create these by hand to test with.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Contact:
    contact_id: str
    name: str
    title: str
    company: str
    company_size: int = 0
    sector: Optional[str] = None
    email_engaged: bool = False      # did they open/click a prior email?
    visited_website: bool = False    # Reo.dev signal
    hubspot_owner_id: Optional[str] = None
    source_event: Optional[str] = None
