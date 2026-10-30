"""Vertical catalogue: SAM labels, SOP documents, rule vocabulary and alert policies."""
from __future__ import annotations

LEVELS = ("NORMAL", "ALERT", "CRITICAL")
RANK = {lvl: i for i, lvl in enumerate(LEVELS)}


def max_level(*levels: str) -> str:
    return max(levels, key=lambda x: RANK.get(x, 0)) if levels else "NORMAL"


VERTICALS: dict[str, dict] = {
    "mall_security": {
        "label": "Mall Security",
        "tenant_type": "mall",
        "sam_labels": ["person", "crowd", "bag", "backpack", "restricted_door", "gate_sign", "fence", "violence", "fire", "smoke", "weapon"],
        "rules": ["unattended_bag", "crowd_density", "restricted_area", "stampede_panic", "medical_distress", "physical_abuse", "fire_smoke", "weapon_visible", "camera_obstructed"],
        # edge actions per level (kinds are executed by the action agent)
        "policy": {
            "ALERT": ["speak", "notify_security"],
            "CRITICAL": ["speak", "notify_security", "whatsapp"],
        },
        "sop": (
            "MALL SECURITY SOP\n"
            "1. Unattended bag: any bag with no person nearby for more than 2 minutes is a suspected "
            "unattended item. Raise ALERT at 2 min; CRITICAL if in a crowded or exit area.\n"
            "2. Crowd density above 50 people in one camera view: ALERT; visible crush or panic: CRITICAL.\n"
            "3. Restricted area entry (staff-only door, fenced zone) by a non-staff person: ALERT.\n"
            "4. Physical abuse or a fight (hitting, slapping, kicking, shoving, choking, dragging someone): CRITICAL immediately, notify security.\n"
            "5. Visible fire or smoke, a weapon being held or brandished: CRITICAL immediately. Panic, running crowds, a person collapsed: CRITICAL.\n"
            "Always include the camera and the objects involved in the reason."
        ),
    },
    "home_seniors": {
        "label": "Home - Seniors",
        "tenant_type": "home_senior",
        "sam_labels": ["person", "stove", "keys", "door", "bed", "floor", "fallen_person", "violence", "fire", "smoke", "weapon"],
        "rules": ["fall_detected", "no_motion_2h", "door_open_night", "stove_on_unattended", "help_called", "medical_distress", "physical_abuse", "fire_smoke", "weapon_visible", "camera_obstructed"],
        "policy": {
            "ALERT": ["speak", "oled"],
            "CRITICAL": ["speak", "oled", "isd1820", "call_family"],
        },
        "sop": (
            "SENIOR FALL & SAFETY PROTOCOL\n"
            "1. Person on the floor or in a fall posture: CRITICAL, speak to them, call family.\n"
            "2. No motion for 2 hours during waking hours: ALERT (check habits, e.g. afternoon nap).\n"
            "3. Front door open at night (22:00-05:00): ALERT unless it matches a known habit.\n"
            "4. Stove on with nobody in the kitchen: ALERT; with visible flame/smoke: CRITICAL.\n"
            "5. A spoken call for help ('help', 'bachao'): CRITICAL.\n"
            "6. Physical abuse of the senior (someone hitting, slapping, shoving, restraining or dragging them): CRITICAL immediately, call family.\n"
            "7. Visible fire or smoke, a weapon, a person clutching their chest or convulsing: CRITICAL.\n"
            "Speak calmly in simple words; seniors may be frightened."
        ),
    },
    "school_children": {
        "label": "School - Children",
        "tenant_type": "school",
        "sam_labels": ["person", "child", "adult", "badge", "gate", "fence", "corridor", "violence", "fire", "smoke", "weapon"],
        "rules": ["child_alone_corridor", "stranger_no_badge", "gate_open_class_time", "bullying_cluster", "fence_climbing", "child_leaving_unescorted", "medical_distress", "physical_abuse", "fire_smoke", "weapon_visible", "camera_obstructed"],
        "policy": {
            "ALERT": ["speak", "notify_admin"],
            "CRITICAL": ["speak", "notify_admin", "whatsapp"],
        },
        "sop": (
            "CHILD SAFETY POLICY\n"
            "1. A child alone in a corridor during class time: ALERT.\n"
            "2. An adult without a visible school badge inside the premises: ALERT; CRITICAL if near children.\n"
            "3. Main gate open during class hours (09:00-15:00) without staff: ALERT.\n"
            "4. A cluster of children surrounding one child with aggressive posture (bullying): ALERT.\n"
            "5. Any adult or child hitting, kicking, slapping, shoving or dragging a child: CRITICAL immediately, notify administration.\n"
            "6. A person climbing the fence or a child leaving through the gate unescorted: ALERT; fire, smoke or a weapon: CRITICAL.\n"
            "Notify school administration; never name or describe children beyond what is needed."
        ),
    },
    "icu_hospital": {
        "label": "ICU - Hospital",
        "tenant_type": "icu_hospital",
        "sam_labels": ["person", "patient", "bed", "floor", "monitor", "staff", "nurse", "violence", "fire", "smoke", "weapon"],
        "rules": ["patient_out_of_bed", "patient_on_floor", "monitor_alarm", "staff_absent_1h", "patient_pulling_lines", "seizure_or_convulsion", "physical_abuse", "fire_smoke", "weapon_visible", "camera_obstructed"],
        "policy": {
            "ALERT": ["speak", "notify_station"],
            "CRITICAL": ["speak", "notify_station", "oled"],
        },
        "sop": (
            "ICU MONITORING PROTOCOL\n"
            "1. Patient out of bed unattended: ALERT; patient on the floor: CRITICAL.\n"
            "2. Monitor showing ALARM or abnormal vitals (e.g. HR above 120 or below 40, SpO2 below 90): "
            "CRITICAL, notify the nurse station with the image proof.\n"
            "3. No staff seen at the bedside for more than 1 hour: ALERT.\n"
            "4. Anyone hitting, restraining roughly or otherwise physically abusing a patient (or staff): CRITICAL immediately.\n"
            "5. Patient pulling at tubes, lines or monitors, or convulsing: CRITICAL. Fire or smoke: CRITICAL.\n"
            "Be factual; this is decision support, a nurse always confirms."
        ),
    },
}


def sop_for(vertical: str) -> str:
    return VERTICALS[vertical]["sop"]
