import io
import base64
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from flask import Flask, request, jsonify, send_from_directory

from engine import run_one_simulation

app = Flask(__name__, static_folder='static')

N_RUNS = 60
BORO_CENTRAL_FIXED = 740
BIN_MIN = 5
N_BINS = 24

DOOR_NAMES = ['PIKE_ACCESS_1', 'PIKE_ACCESS_2', 'ACCESS_3', 'ACCESS_4']
GARAGE_LABELS = {'boro_central': 'Boro Central', 'clydes': "Clyde's Site",
                  '8251': '8251 Greensboro', 'combined': 'All Lots Combined'}


def clock_label(min_since_4pm):
    total_min = 16 * 60 + min_since_4pm
    h = int(total_min // 60) % 24
    m = int(total_min % 60)
    ampm = 'PM' if h >= 12 else 'AM'
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f'{h12}:{m:02d} {ampm}'


def bin_index(ready_min):
    return min(int(ready_min // BIN_MIN), N_BINS - 1)


def run_scenario(employees_8251, employees_clydes, disabled_doors, light_cycles):
    garage_counts = {'boro_central': BORO_CENTRAL_FIXED, 'clydes': employees_clydes,
                      '8251': employees_8251}
    all_runs = []
    for i in range(N_RUNS):
        cars = run_one_simulation(
            garage_counts, seed=i, disabled_doors=set(disabled_doors),
            pike_green_sec=light_cycles['pike_green'], pike_red_sec=light_cycles['pike_red'],
            access3_green_sec=light_cycles['access3_green'], access3_red_sec=light_cycles['access3_red'],
            access4_green_sec=light_cycles['access4_green'], access4_red_sec=light_cycles['access4_red'],
        )
        all_runs.append(cars)
    return all_runs, garage_counts


def per_run_avg(garage, cars_one_run, all_garages=False):
    if all_garages:
        waits = [(c['done_sec'] - c['ready_sec']) / 60.0 for c in cars_one_run
                 if c['done_sec'] is not None and 60 <= c['ready_min'] < 120]
    else:
        waits = [(c['done_sec'] - c['ready_sec']) / 60.0 for c in cars_one_run
                 if c['garage'] == garage and c['done_sec'] is not None
                 and 60 <= c['ready_min'] < 120]
    return np.mean(waits) if waits else None


def stranded_fraction(garage, cars_one_run, all_garages=False):
    if all_garages:
        relevant = [c for c in cars_one_run if 60 <= c['ready_min'] < 120]
    else:
        relevant = [c for c in cars_one_run if c['garage'] == garage and 60 <= c['ready_min'] < 120]
    if not relevant:
        return 0.0
    stranded = sum(1 for c in relevant if c['done_sec'] is None)
    return stranded / len(relevant)


def bin_curve(garage, cars_one_run, all_garages=False):
    bin_sums = np.zeros(N_BINS)
    bin_counts = np.zeros(N_BINS)
    for c in cars_one_run:
        if not all_garages and c['garage'] != garage:
            continue
        if c['done_sec'] is None:
            continue
        b = bin_index(c['ready_min'])
        bin_sums[b] += (c['done_sec'] - c['ready_sec']) / 60.0
        bin_counts[b] += 1
    return np.divide(bin_sums, bin_counts, out=np.zeros(N_BINS), where=bin_counts > 0), bin_counts


def make_chart_base64(garage_label, avg_wait, bin_counts):
    x = np.arange(N_BINS) * BIN_MIN + BIN_MIN / 2
    fig, ax = plt.subplots(figsize=(7, 3.5))
    valid = bin_counts > 0
    ax.plot(x[valid], avg_wait[valid], color='#1677AE', linewidth=2)
    tick_positions = np.arange(0, 121, 15)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels([clock_label(t) for t in tick_positions], rotation=0, fontsize=8)
    ax.set_ylabel('Minutes to exit')
    ax.set_title(f'{garage_label} \u2014 Median Simulated Day', fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 120)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/simulate', methods=['POST'])
def simulate():
    body = request.get_json()
    employees_8251 = int(body.get('employees_8251', 1200))
    employees_clydes = int(body.get('employees_clydes', 200))
    disabled_doors = body.get('disabled_doors', [])
    light_cycles = body.get('light_cycles', {})

    defaults = {'pike_green': 90, 'pike_red': 30, 'access3_green': 30, 'access3_red': 60,
                'access4_green': 30, 'access4_red': 60}
    for k, v in defaults.items():
        light_cycles.setdefault(k, v)
        light_cycles[k] = max(15, min(120, int(round(light_cycles[k] / 15.0) * 15)))

    all_runs, garage_counts = run_scenario(employees_8251, employees_clydes, disabled_doors, light_cycles)

    results = {}
    for garage in list(garage_counts.keys()) + ['combined']:
        all_garages = garage == 'combined'
        if not all_garages and garage_counts[garage] == 0:
            results[garage] = {'avg': None, 'max': None, 'stranded_pct': None,
                                'chart': None, 'no_employees': True}
            continue
        run_avgs = [(idx, per_run_avg(garage, cars, all_garages)) for idx, cars in enumerate(all_runs)]
        run_avgs = [(i, a) for i, a in run_avgs if a is not None]
        if not run_avgs:
            results[garage] = {'avg': None, 'max': None, 'stranded_pct': 100.0,
                                'chart': None, 'no_employees': False}
            continue
        run_avgs.sort(key=lambda x: x[1])
        median_idx, median_avg = run_avgs[len(run_avgs) // 2]
        median_cars = all_runs[median_idx]
        avg_wait, bin_counts = bin_curve(garage, median_cars, all_garages)
        chart_peak = float(avg_wait.max()) if bin_counts.sum() > 0 else None
        stranded_pcts = [stranded_fraction(garage, cars, all_garages) for cars in all_runs]
        chart_b64 = make_chart_base64(GARAGE_LABELS[garage], avg_wait, bin_counts) if bin_counts.sum() > 0 else None
        results[garage] = {
            'avg': round(median_avg, 2) if median_avg else None,
            'max': round(chart_peak, 2) if chart_peak else None,
            'stranded_pct': round(np.mean(stranded_pcts) * 100, 1),
            'chart': chart_b64,
            'no_employees': False,
        }

    return jsonify({'results': results, 'n_runs': N_RUNS, 'light_cycles': light_cycles})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
