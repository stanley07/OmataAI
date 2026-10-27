"""Server-controlled synthetic WhatsApp authorization.

Never derive routes or approval rights from an incoming HTTP body.
"""

import os

def get_staging_approval_routes():
    if os.environ.get("OMATA_PG_WHATSAPP_HTTP_TEST") != "enabled":
        return {}

    if os.environ.get("OMATA_PG_SYNTHETIC_ROUTES") != "enabled":
        return {}

    return {
        ("123456789012345", "2348000000000"): {
            "merchant": "OMATA_ATOMIC_TEST",
            "can_approve": True,
        }
    }
