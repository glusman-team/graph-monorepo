-- Join KGX edges to their subject and object nodes and emit whole documents as JSON text.
--
-- Why the engine does this and not Go: the nodes file is the lookup side, and ClickHouse
-- hash-joins it once instead of Go holding a map of 100k+ node records or re-scanning the
-- file per edge. The edges stream through, so a 134 MB file never has to fit in memory.
--
-- JSONAsObject is what maps one whole NDJSON line into a JSON column; JSONEachRow with the
-- same structure yields {} unless every line is wrapped as {"col": {...}} (ADR 0002).
-- Paths must be absolute: file() resolves relative to the process CWD.
--
-- A node that does not exist yields the JSON default ({}), never a SQL NULL, so the caller
-- sees an empty object and omits the field. Emitting a null here would violate the contract.
SELECT
    edges.edge.id::String     AS id,
    toJSONString(edges.edge)  AS edge,
    toJSONString(snode.node)  AS subject_node,
    toJSONString(onode.node)  AS object_node
FROM file({edges}, 'JSONAsObject', 'edge JSON') AS edges
LEFT JOIN file({nodes}, 'JSONAsObject', 'node JSON') AS snode
    ON edges.edge.subject::String = snode.node.id::String
LEFT JOIN file({nodes}, 'JSONAsObject', 'node JSON') AS onode
    ON edges.edge.object::String = onode.node.id::String
SETTINGS max_threads = {threads}
