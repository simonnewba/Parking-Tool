"""
Network topology and parameters for the garage-exit Monte Carlo model.

Distances are pixel-calibrated against the user-supplied 550ft reference
segment. Where a distance was position-estimated rather than directly
measured, it is flagged in DISTANCE_NOTES.
"""

# ---- Fixed physical/behavioral assumptions (documented, not invented) ----
SPACING_FT = 25.0            # jam spacing, ft/vehicle (reused throughout)
CV = 0.6                     # gap coefficient of variation (reused from dashboard)

# External-door critical gap (unchanged from validated dashboard model)
EXTERNAL_CRITICAL_GAP_SEC = 5.0

# Internal LEFT-TURN critical gap: HCM guidance calls for a distinctly larger
# critical gap for minor-street left turns across two-way traffic than for
# simpler merges. Literature examples for this movement type run well above
# the external-door value; using 7.0s as a literature-grounded midpoint.
INTERNAL_LEFT_TURN_CRITICAL_GAP_SEC = 7.0

# Follow-up time: HCM-documented ratio of follow-up time to critical gap is
# consistently ~0.60 across studies. Applied to both gap types.
FOLLOWUP_RATIO = 0.60

# BPR speed-flow parameters (reused from validated dashboard model)
BPR_ALPHA = 0.8
BPR_BETA = 4.7

# Internal road free-flow speed and breakdown threshold
INTERNAL_FREE_FLOW_MPH = 15.0
BREAKDOWN_FRACTION = 0.30    # below 30% of free-flow -> switch to queue-discharge/FCFS

# Pike / Greensboro external-door parameters (unchanged, validated)
PIKE_FREE_FLOW_MPH = 55.0
PIKE_LANES = 4
PIKE_CAPACITY_VPH_LANE = 1700
PIKE_SEG_LENGTH_FT = 2000
ACCESS1_THRESHOLD_FT = 1400
ACCESS2_THRESHOLD_FT = 1700
PIKE_VOLUME_VPH = 6000
PIKE_GREEN_SEC = 90
PIKE_RED_SEC = 30
PIKE_RED_RATE = 0.5          # cars/sec, unopposed clearing during red

GREENSBORO_FREE_FLOW_MPH = 25.0
GREENSBORO_CAPACITY_VPH_LANE = 1700
GREENSBORO_THROUGH_LANES = 1
GREENSBORO_STORAGE_LANES = 2
GREENSBORO_VOLUME_VPH = 460
ACCESS3_SEG_LENGTH_FT = 750
ACCESS4_SEG_LENGTH_FT = 400
ACCESS3_GREEN_SEC, ACCESS3_RED_SEC = 30, 60
ACCESS4_GREEN_SEC, ACCESS4_RED_SEC = 30, 60
GREENSBORO_TURN_RATE = 0.5   # cars/sec, protected turn during green

# Distribution shape shared by employee departures AND general background traffic
DIST_SLOTS_MIN = [0, 15, 30, 45, 60, 75, 90, 105]
DIST_PCTS = [4, 6, 10, 14, 25, 25, 10, 6]

# General background volumes on internal roads (vehicles/hour, ONE direction;
# assumed equal in both directions per user confirmation)
BACKGROUND_VPH = {
    'access4_loop': 75,     # "Solutions 75 vph"
    'access3_road': 460,    # "Greensboro Dr 460 vph"
    'pinnacle_dr': 50,      # "Pinnacle Dr 50 vph"
    'southern_curve': 40,   # "40 vehicles/hour"
}

# ---- Network nodes ----
# Garage exits (6 total, confirmed)
GARAGE_EXITS = {
    'boro_exit':            {'garage': 'boro_central'},
    'clyde_north_exit':      {'garage': 'clydes'},
    'clyde_south_exit':      {'garage': 'clydes'},
    '8251_north_exit':       {'garage': '8251'},
    '8251_east_exit':        {'garage': '8251'},
    '8251_south_exit':       {'garage': '8251'},
}

# ---- Network edges: (node_a, node_b, distance_ft, kind) ----
# kind: 'internal_stub' (Boro's own capped stub), 'internal_uncontrolled'
# (gap-acceptance onto priority road), 'internal_link' (plain link/no gap
# check needed, e.g. shared corridor interior), 'loop_only' (8251 N/E -> light,
# no other connection)
# ---- Network edges: (node_a, node_b, distance_ft, kind, queue_at) ----
# queue_at explicitly names which endpoint is the controlled/minor side where
# a car actually waits and performs gap acceptance (removes ambiguity about
# which tuple position is "controlled" -- roads are two-way, so this can't be
# inferred from travel direction alone).
# kind: 'internal_stub_capped4' (Boro's own capped stub), 'internal_uncontrolled'
# (gap-acceptance onto priority road), 'internal_link' (plain link/no gap
# check needed), 'loop_only' (8251 N/E -> light, no other connection)
EDGES = [
    # Boro Central
    ('boro_exit', 'boro_pike_stub', 121, 'internal_stub_capped4', 'boro_pike_stub'),
    ('boro_pike_stub', 'PIKE_ACCESS_2', 0, 'external_pike2', None),
    ('boro_exit', 'metro_stop', 428, 'internal_uncontrolled', 'metro_stop'),
    ('metro_stop', 'PIKE_ACCESS_1', 0, 'external_pike1', None),  # ASSUMPTION: metro stop is Access 1
    ('metro_stop', 'shared_junction', 373, 'internal_link', None),
    ('metro_stop', 'access4_stop', 616, 'internal_uncontrolled', 'access4_stop'),
    ('access4_stop', 'ACCESS_4', 0, 'external_greensboro4', None),

    # Shared junction <-> Clyde's north / 8251 south (face each other across the
    # street) -- the garage-exit side is always the controlled/queueing node.
    ('shared_junction', 'clyde_north_exit', 60, 'internal_uncontrolled', 'clyde_north_exit'),
    ('shared_junction', '8251_south_exit', 60, 'internal_uncontrolled', '8251_south_exit'),

    # 8251's own loop (north/east exits -> nearby light only, no other link)
    ('8251_north_exit', 'access3_light', 200, 'loop_only', None),
    ('8251_east_exit', 'access3_light', 230, 'loop_only', None),
    ('access3_light', 'ACCESS_3', 0, 'external_greensboro3', None),

    # Pinnacle Dr: 8251-south / shared_junction also reaches Pinnacle Dr directly.
    # The merge point onto Pinnacle Dr itself is the controlled side.
    ('shared_junction', 'pinnacle_dr_node', 200, 'internal_uncontrolled', 'pinnacle_dr_node'),
    ('pinnacle_dr_node', 'access3_light', 437, 'internal_link', None),

    # Southern curve: reachable from the shared junction directly (confirmed via
    # the "reaches everywhere" blue-dot trace) AS WELL AS from Clyde's south exit.
    ('shared_junction', 'southern_curve_node', 200, 'internal_link', None),  # ASSUMPTION: distance estimated
    ('clyde_south_exit', 'southern_curve_node', 197, 'internal_uncontrolled', 'clyde_south_exit'),
    ('southern_curve_node', 'far_stop', 871, 'internal_link', None),
    ('far_stop', 'tysons_light', 1026, 'internal_uncontrolled', 'far_stop'),
    ('tysons_light', 'ACCESS_3_ALT', 0, 'external_greensboro3', None),  # reaches same Access 3 door
]

DISTANCE_NOTES = (
    "Distances calibrated against the 550ft reference segment (measured "
    "222.3px -> 2.474 ft/px). Direct icon-to-icon measurement showed a "
    "~12% gap versus the known 550ft value (616ft measured), so treat all "
    "distances as accurate to roughly +/-10-12%, not exact. The 8251 loop "
    "connector and southern curve lengths are position-estimated rather "
    "than directly traced, and are the least certain figures in the set."
)
