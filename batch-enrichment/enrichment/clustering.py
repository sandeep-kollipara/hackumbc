import json
from .repository import initial_object_id


def cluster(repository, threshold=0.15, neighbors=30, across_users=False):
    """Connected components of the approximate k-nearest-neighbor graph."""
    parent = {}

    def find(node):
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != node:
            next_node = parent[node]
            parent[node] = root
            node = next_node
        return root

    for record_id, vector, document in repository.image_rows():
        data = json.loads(document)
        find(record_id)
        where = None if across_users else {"username": data["username"]}
        matches = repository.search(vector.tolist(), limit=neighbors + 1,
                                    threshold=threshold, where=where)
        for match in matches:
            left, right = find(record_id), find(match["record_id"])
            if left != right:
                parent[max(left, right)] = min(left, right)
    assignments = {record_id: initial_object_id(find(record_id)) for record_id in parent}
    repository.assign_ids(assignments)
    return {"records": len(assignments), "objects": len(set(assignments.values()))}
