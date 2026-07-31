"""
Full single-run discrete-event simulation.

Approach: each car, at its ready_time, evaluates all viable routes to
external doors using CURRENT queue lengths (reactive routing), picks the
best, then is processed hop-by-hop through the chosen path. Internal
merges use HCM-style gap acceptance (with follow-up time platooning) or,
if BPR speed has broken down below threshold, FCFS queue-discharge. Every
internal segment has a physical capacity; a full segment is removed from
the routing choice set for that decision. External doors reuse the
validated dashboard mechanics exactly.
"""
import numpy as np
import heapq
import network_config as cfg
from network_graph import enumerate_all_routes, EXTERNAL_DOORS

GARAGE_TO_EXITS = {}
for node, info in cfg.GARAGE_EXITS.items():
    GARAGE_TO_EXITS.setdefault(info['garage'], []).append(node)

ROUTES = enumerate_all_routes()


def dist_pct_at(t_min):
    slot = min(int(t_min // 15), 7)
    return cfg.DIST_PCTS[slot] / 100.0


def gamma_headway(rng, rate_per_hour):
    if rate_per_hour <= 1e-6:
        return 1e9
    avg_gap = 3600.0 / rate_per_hour
    shape = 1.0 / (cfg.CV ** 2)
    scale = avg_gap * (cfg.CV ** 2)
    return rng.gamma(shape, scale)


class SegmentState:
    """Tracks physical occupancy (spillback capacity) and recent-pass rate
    (endogenous traffic) for one internal edge."""
    def __init__(self, length_ft):
        self.capacity = max(1, int(length_ft / cfg.SPACING_FT)) if length_ft > 0 else 999
        self.occupancy = 0
        self.recent_passes = []

    def is_full(self):
        return self.occupancy >= self.capacity

    def endogenous_vph(self, t_now, window=90.0):
        self.recent_passes = [t for t in self.recent_passes if t_now - t <= window]
        return len(self.recent_passes) * (3600.0 / window)

    def record_pass(self, t_now):
        self.recent_passes.append(t_now)


class QueueState:
    """FIFO queue at an internal merge point. Tracks next_available_time:
    the moment the queue is free to start serving a fresh arrival. Cars
    processed in ready-time order correctly inherit delay from earlier
    cars via this persistent state -- this is what makes FCFS queueing
    (not just independent per-car gap draws) actually work."""
    def __init__(self):
        self.next_available_time = 0.0
        self.had_to_wait_last = False


def run_one_simulation(garage_employee_counts, seed=None, horizon_min=125, dt_report=True,
                        pike_red_rate=None, trace_ids=None, disabled_doors=None,
                        pike_green_sec=None, pike_red_sec=None,
                        access3_green_sec=None, access3_red_sec=None,
                        access4_green_sec=None, access4_red_sec=None):
    """pike_red_rate overrides the default unopposed red-phase clearing rate
    (cfg.PIKE_RED_RATE). disabled_doors: set of door names ('PIKE_ACCESS_1',
    'PIKE_ACCESS_2', 'ACCESS_3', 'ACCESS_4') to treat as fully closed --
    removed from every car's routing choice set entirely, simulating a
    blocked/closed road. *_green_sec/*_red_sec override the default signal
    timing at each of the 3 signal-controlled points."""
    if pike_red_rate is None:
        pike_red_rate = cfg.PIKE_RED_RATE
    if trace_ids is None:
        trace_ids = set()
    if disabled_doors is None:
        disabled_doors = set()
    pike_green_sec = pike_green_sec if pike_green_sec is not None else cfg.PIKE_GREEN_SEC
    pike_red_sec = pike_red_sec if pike_red_sec is not None else cfg.PIKE_RED_SEC
    access3_green_sec = access3_green_sec if access3_green_sec is not None else cfg.ACCESS3_GREEN_SEC
    access3_red_sec = access3_red_sec if access3_red_sec is not None else cfg.ACCESS3_RED_SEC
    access4_green_sec = access4_green_sec if access4_green_sec is not None else cfg.ACCESS4_GREEN_SEC
    access4_red_sec = access4_red_sec if access4_red_sec is not None else cfg.ACCESS4_RED_SEC
    rng = np.random.default_rng(seed)

    # ---- Generate demand ----
    cars = []
    cid = 0
    for garage, total_emp in garage_employee_counts.items():
        exits = GARAGE_TO_EXITS[garage]
        for i, slot_start in enumerate(cfg.DIST_SLOTS_MIN):
            pct = cfg.DIST_PCTS[i] / 100.0
            mean_count = total_emp * pct
            n = rng.poisson(mean_count)
            times = rng.uniform(slot_start, slot_start + 15, size=n)
            for t in times:
                exit_node = exits[rng.integers(0, len(exits))]
                cars.append({'id': cid, 'garage': garage, 'exit': exit_node,
                             'ready_min': t, 'ready_sec': t * 60.0, 'done_sec': None})
                cid += 1
    cars.sort(key=lambda c: c['ready_sec'])

    # ---- Build segment/queue state for every internal edge ----
    segments = {}
    for a, b, dist, kind, queue_at in cfg.EDGES:
        if kind.startswith('internal'):
            segments[(a, b)] = SegmentState(dist)
            segments[(b, a)] = segments[(a, b)]  # shared physical road, shared occupancy
    queues = {}
    for a, b, dist, kind, queue_at in cfg.EDGES:
        if queue_at is not None:
            queues[queue_at] = QueueState()

    # Boro's stub gets its explicit 4-car cap (overrides length-derived capacity)
    if ('boro_exit', 'boro_pike_stub') in segments:
        segments[('boro_exit', 'boro_pike_stub')].capacity = 4

    # External door state (mirrors validated dashboard mechanics)
    ext_state = {
        'PIKE_ACCESS_1': {'queue': [], 'blocked_until': 0},
        'PIKE_ACCESS_2': {'queue': [], 'blocked_until': 0},
        'ACCESS_3': {'queue': []},
        'ACCESS_3_ALT': {'queue': []},
        'ACCESS_4': {'queue': []},
    }

    def pike_blocked(t_sec, threshold_ft):
        t_min = t_sec / 60.0
        pct = dist_pct_at(t_min)
        vol_all = cfg.PIKE_VOLUME_VPH * pct / 0.25
        vol_per_lane = vol_all / cfg.PIKE_LANES
        speed = cfg.PIKE_FREE_FLOW_MPH / (1 + cfg.BPR_ALPHA * (vol_per_lane / cfg.PIKE_CAPACITY_VPH_LANE) ** cfg.BPR_BETA)
        cars_in_transit = vol_all * (cfg.PIKE_SEG_LENGTH_FT / 5280) / speed
        queue_ft = (cars_in_transit / cfg.PIKE_LANES) * cfg.SPACING_FT
        return queue_ft > threshold_ft

    def greensboro_blocked(t_sec, seg_length_ft):
        t_min = t_sec / 60.0
        pct = dist_pct_at(t_min)
        vol_all = cfg.GREENSBORO_VOLUME_VPH * pct / 0.25
        vol_per_lane = vol_all / cfg.GREENSBORO_THROUGH_LANES
        speed = cfg.GREENSBORO_FREE_FLOW_MPH / (1 + cfg.BPR_ALPHA * (vol_per_lane / cfg.GREENSBORO_CAPACITY_VPH_LANE) ** cfg.BPR_BETA)
        cars_in_transit = vol_all * (seg_length_ft / 5280) / speed
        queue_ft = (cars_in_transit / cfg.GREENSBORO_STORAGE_LANES) * cfg.SPACING_FT
        return queue_ft > seg_length_ft

    def pike_rate(t_sec, blocked):
        if blocked:
            return 0.0
        cycle = pike_green_sec + pike_red_sec
        phase_t = t_sec % cycle
        if phase_t < pike_green_sec:
            t_min = t_sec / 60.0
            pct = dist_pct_at(t_min)
            vol_all = cfg.PIKE_VOLUME_VPH * pct / 0.25
            vol_per_lane = vol_all / cfg.PIKE_LANES
            avg_gap = 3600.0 / vol_per_lane if vol_per_lane > 0 else 1e9
            shape = 1.0 / (cfg.CV ** 2)
            scale = avg_gap * (cfg.CV ** 2)
            from scipy.stats import gamma as gamma_dist
            p_accept = 1 - gamma_dist.cdf(cfg.EXTERNAL_CRITICAL_GAP_SEC, shape, scale=scale)
            return p_accept / avg_gap if avg_gap > 0 else 0.0
        else:
            return pike_red_rate

    def greensboro_rate(t_sec, blocked, green_sec, red_sec):
        if blocked:
            return 0.0
        cycle = green_sec + red_sec
        return cfg.GREENSBORO_TURN_RATE if (t_sec % cycle) < green_sec else 0.0

    # ---- Main event loop: process cars in ready-time order, each fully
    # simulated hop-by-hop before the next car is considered for routing
    # (routing decisions still reflect real-time queue/segment state built
    # up by all previously-processed cars, preserving the endogenous effect) ----
    for car in cars:
        t = car['ready_sec']
        exit_node = car['exit']
        def _door_name(n):
            return 'ACCESS_3' if n == 'ACCESS_3_ALT' else n
        candidate_routes = [r for r in ROUTES[exit_node] if _door_name(r[0][-1]) not in disabled_doors]
        if not candidate_routes:
            # Every viable door for this exit is closed -- car can never complete.
            continue
        is_traced = car['id'] in trace_ids
        if is_traced:
            car['trace'] = {'route_scores': [], 'hops': []}

        # Reactive routing: score each candidate route by current total
        # queue backlog + full-segment penalty along it; pick the minimum.
        def score_route(route):
            path, dists, kinds, queue_ats = route
            score = 0.0
            for i in range(len(path) - 1):
                qa = queue_ats[i]
                if qa is not None and qa in queues:
                    score += max(0.0, queues[qa].next_available_time - t)
                edge = (path[i], path[i+1])
                if edge in segments and segments[edge].is_full():
                    score += 1000  # effectively removes a full segment from consideration
            return score

        if is_traced:
            for route in candidate_routes:
                car['trace']['route_scores'].append((route[0], score_route(route)))

        best_route = min(candidate_routes, key=score_route)
        path, dists, kinds, queue_ats = best_route
        if is_traced:
            car['trace']['chosen_path'] = path

        # Walk the path hop by hop
        cur_t = t
        blocked_route = False
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            kind = kinds[i]
            dist = dists[i]
            queue_at = queue_ats[i]

            if kind.startswith('internal'):
                seg = segments[(a, b)]
                if seg.is_full():
                    blocked_route = True
                    break
                seg.occupancy += 1
                # Travel time via BPR (endogenous + relevant background)
                bg_key = {'pinnacle_dr_node': 'pinnacle_dr', 'access4_stop': 'access4_loop',
                          'access3_light': 'access3_road', 'southern_curve_node': 'southern_curve'}.get(b, None)
                bg_vph = cfg.BACKGROUND_VPH.get(bg_key, 30)
                endo_vph = seg.endogenous_vph(cur_t)
                total_vph = bg_vph + endo_vph
                speed = cfg.INTERNAL_FREE_FLOW_MPH / (1 + cfg.BPR_ALPHA * (total_vph / 400.0) ** cfg.BPR_BETA)
                floor_speed = cfg.INTERNAL_FREE_FLOW_MPH * cfg.BREAKDOWN_FRACTION
                breakdown = speed < floor_speed
                speed = max(speed, floor_speed * 0.3)  # hard numerical floor, avoids div-by-zero
                travel_sec = (dist / 5280.0) / speed * 3600.0
                t_before_travel = cur_t
                cur_t += travel_sec
                seg.record_pass(cur_t)
                seg.occupancy = max(0, seg.occupancy - 1)

                if 'uncontrolled' in kind:
                    q = queues[queue_at]
                    tc = cfg.INTERNAL_LEFT_TURN_CRITICAL_GAP_SEC
                    tf = tc * cfg.FOLLOWUP_RATIO
                    arrival_t = cur_t
                    had_to_wait = arrival_t < q.next_available_time
                    service_start = max(arrival_t, q.next_available_time)
                    queue_backlog_sec = max(0.0, q.next_available_time - arrival_t)

                    if breakdown:
                        # FCFS queue-discharge: served at the floor-speed-derived rate
                        discharge_rate = floor_speed * 5280.0 / 3600.0 / cfg.SPACING_FT
                        service_time = 1.0 / max(discharge_rate, 0.05)
                        cur_t = service_start + service_time
                        merge_mode = 'FCFS queue-discharge (traffic breakdown)'
                    elif had_to_wait:
                        # This car is joining an already-active queue -- it benefits
                        # from follow-up time rather than a full fresh critical-gap
                        # search (the HCM platooning effect), rather than each queued
                        # car independently re-searching from scratch.
                        cur_t = service_start + tf
                        merge_mode = 'follow-up (joined active queue)'
                    else:
                        # Queue was idle: fresh gap-acceptance search from scratch
                        sim_t = service_start
                        while True:
                            h = gamma_headway(rng, total_vph)
                            if h >= tc:
                                sim_t += tc
                                break
                            else:
                                sim_t += h
                        cur_t = sim_t
                        merge_mode = 'fresh gap search (queue was idle)'

                    q.next_available_time = cur_t

                    if is_traced:
                        car['trace']['hops'].append({
                            'segment': f'{a} -> {b}', 'travel_sec': travel_sec,
                            'conflicting_vph': total_vph, 'queue_backlog_sec': queue_backlog_sec,
                            'merge_mode': merge_mode, 'merge_wait_sec': cur_t - arrival_t,
                            'arrived_at': t_before_travel, 'departed_at': cur_t,
                        })
                elif is_traced:
                    car['trace']['hops'].append({
                        'segment': f'{a} -> {b}', 'travel_sec': travel_sec,
                        'conflicting_vph': None, 'queue_backlog_sec': 0,
                        'merge_mode': 'plain link, no merge needed', 'merge_wait_sec': 0,
                        'arrived_at': t_before_travel, 'departed_at': cur_t,
                    })

            elif kind.startswith('external_'):
                t_arrive = cur_t
                if b in ('PIKE_ACCESS_1', 'PIKE_ACCESS_2'):
                    threshold = cfg.ACCESS1_THRESHOLD_FT if b == 'PIKE_ACCESS_1' else cfg.ACCESS2_THRESHOLD_FT
                    blocked = pike_blocked(cur_t, threshold)
                    rate = pike_rate(cur_t, blocked)
                    door_desc = f'{b} (Pike, blocked={blocked})'
                else:
                    seg_len = cfg.ACCESS3_SEG_LENGTH_FT if 'ACCESS_3' in b else cfg.ACCESS4_SEG_LENGTH_FT
                    green_s = access3_green_sec if 'ACCESS_3' in b else access4_green_sec
                    red_s = access3_red_sec if 'ACCESS_3' in b else access4_red_sec
                    blocked = greensboro_blocked(cur_t, seg_len)
                    rate = greensboro_rate(cur_t, blocked, green_s, red_s)
                    door_desc = f'{b} (Greensboro, blocked={blocked})'
                wait = rng.exponential(1.0 / rate) if rate > 1e-6 else 300.0
                cur_t += wait
                if is_traced:
                    car['trace']['hops'].append({
                        'segment': f'{a} -> {b}', 'travel_sec': 0,
                        'conflicting_vph': None, 'queue_backlog_sec': 0,
                        'merge_mode': f'external door merge: {door_desc}', 'merge_wait_sec': wait,
                        'arrived_at': t_arrive, 'departed_at': cur_t,
                    })

            elif kind == 'internal_link' or kind == 'loop_only':
                t_arrive = cur_t
                travel_sec = (dist / 5280.0) / cfg.INTERNAL_FREE_FLOW_MPH * 3600.0
                cur_t += travel_sec
                if is_traced:
                    car['trace']['hops'].append({
                        'segment': f'{a} -> {b}', 'travel_sec': travel_sec,
                        'conflicting_vph': None, 'queue_backlog_sec': 0,
                        'merge_mode': 'plain link, no merge needed', 'merge_wait_sec': 0,
                        'arrived_at': t_arrive, 'departed_at': cur_t,
                    })

        if not blocked_route:
            car['done_sec'] = cur_t

    return cars
