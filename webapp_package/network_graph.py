"""Graph utilities: build adjacency, enumerate simple paths from each garage
exit to each external door."""
import network_config as cfg
from collections import defaultdict

EXTERNAL_DOORS = ['PIKE_ACCESS_1', 'PIKE_ACCESS_2', 'ACCESS_3', 'ACCESS_3_ALT', 'ACCESS_4']


def build_adjacency():
    adj = defaultdict(list)
    for a, b, dist, kind, queue_at in cfg.EDGES:
        adj[a].append((b, dist, kind, queue_at))
        adj[b].append((a, dist, kind, queue_at))
    return adj


def find_all_paths(adj, start, is_target, forbidden=(), max_depth=8):
    """DFS enumeration of simple paths from start to any node satisfying is_target.
    `forbidden` nodes (e.g. OTHER garages' own exit points) can never be used
    as through-nodes -- a car from Clyde's can't physically drive through
    Boro Central's private driveway to reach the Pike."""
    results = []

    def dfs(node, path, dists, kinds, queue_ats, visited):
        if len(path) > 1 and is_target(node):
            results.append((list(path), list(dists), list(kinds), list(queue_ats)))
            return
        if len(path) > max_depth:
            return
        for nxt, dist, kind, queue_at in adj[node]:
            if nxt in visited or nxt in forbidden:
                continue
            visited.add(nxt)
            path.append(nxt)
            dists.append(dist)
            kinds.append(kind)
            queue_ats.append(queue_at)
            dfs(nxt, path, dists, kinds, queue_ats, visited)
            path.pop(); dists.pop(); kinds.pop(); queue_ats.pop()
            visited.remove(nxt)

    dfs(start, [start], [], [], [], {start})
    return results


def enumerate_all_routes():
    """For every garage exit, find all simple paths to every external door.
    Other garages' own exit nodes are forbidden as through-nodes."""
    adj = build_adjacency()
    routes = {}
    for exit_node in cfg.GARAGE_EXITS:
        forbidden = set(cfg.GARAGE_EXITS) - {exit_node}
        paths = find_all_paths(adj, exit_node, lambda n: n in EXTERNAL_DOORS, forbidden=forbidden)
        routes[exit_node] = paths
    return routes


if __name__ == '__main__':
    routes = enumerate_all_routes()
    for exit_node, paths in routes.items():
        print(f'\n=== {exit_node} ({len(paths)} routes) ===')
        for path, dists, kinds, queue_ats in paths:
            total = sum(dists)
            print(f'  {" -> ".join(path)}  [{total:.0f} ft]')
