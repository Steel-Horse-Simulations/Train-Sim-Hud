# TSW Hud [v2.17.0] [main] [main]
# Last modified: 2025-08-01
# Known Trains locomotive profile database

LOCO_PROFILES = {
    # Class 66
    "RVM_Class_66_C": {
        "name": "Class 66",
        "display_name": "Class 66",
        "max_speed": 75,  # mph
        "power_type": "diesel",
        "image": "class_66.png"
    },
    # Add more locomotive profiles as needed
}

def get_loco_profile(class_name):
    """Retrieve locomotive profile by raw class name"""
    return LOCO_PROFILES.get(class_name, {
        "name": "Unknown",
        "display_name": class_name,
        "max_speed": 0,
        "power_type": "unknown"
    })
