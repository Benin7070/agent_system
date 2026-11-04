"""Apple-inspired typography and styling constants for AgentCTL UI."""

COLOR_BG_OVERLAY = "rgba(9, 9, 11, 0.72)"
COLOR_BLUR_CARD = "rgba(255, 255, 255, 0.08)"
COLOR_CARD_BORDER = "rgba(255, 255, 255, 0.12)"
COLOR_TEXT_PRIMARY = "#F4F4F5"
COLOR_TEXT_SECONDARY = "#71717A"
COLOR_TEXT_DISCARDED = "rgba(244, 244, 245, 0.40)"

# GLSL stop color triples (R, G, B in 0.0 - 1.0)
GLOW_COLORS = {
    0: {  # IDLE
        "a": (0.376, 0.647, 0.980),  # #60A5FA
        "b": (0.655, 0.545, 0.980),  # #A78BFA
        "c": (0.506, 0.549, 0.973),  # #818CF8
    },
    1: {  # LISTENING
        "a": (0.506, 0.549, 0.973),  # #818CF8
        "b": (0.753, 0.518, 0.988),  # #C084FC
        "c": (0.220, 0.741, 0.973),  # #38BDF8
    },
    2: {  # PROCESSING
        "a": (0.961, 0.620, 0.043),  # #F59E0B
        "b": (0.937, 0.267, 0.267),  # #EF4444
        "c": (0.545, 0.361, 0.965),  # #8B5CF6
    },
    3: {  # SUCCESS
        "a": (0.204, 0.827, 0.600),  # #34D399
        "b": (0.204, 0.827, 0.600),
        "c": (0.204, 0.827, 0.600),
    },
    4: {  # ERROR
        "a": (0.973, 0.443, 0.443),  # #F87171
        "b": (0.973, 0.443, 0.443),
        "c": (0.973, 0.443, 0.443),
    },
}

TRANSCRIPT_CARD_STYLE = f"""
QFrame#transcriptCard {{
    background-color: {COLOR_BLUR_CARD};
    border: 1px solid {COLOR_CARD_BORDER};
    border-radius: 18px;
}}
QLabel#transcriptText {{
    color: {COLOR_TEXT_PRIMARY};
    font-family: 'Inter', 'SF Pro Text', system-ui, sans-serif;
    font-size: 15px;
    font-weight: normal;
    background: transparent;
    padding: 12px 14px;
}}
"""

