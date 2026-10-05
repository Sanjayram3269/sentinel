# osm_urban scenario metadata

Reference data for the imported Bengaluru street network, `network_key = osm_urban_v1`.

This directory is **metadata only**. No simulation adapter is bound to it.
`SimulationRunner`, `SumoTraCIAdapter` and `ClearPathSafetyGuard` stay bound to
`../clearpath_demo`, which is unchanged and remains authoritative for the
pre-emption regression suite. Binding SUMO to this network is later work.

`scenario.json` records the projection metadata the importer reads back out of
the network itself, plus the expected counts for the reference import.

## The network file is not committed

`simulation/networks/osm_urban_v1/osm.net.xml` is ~2 MB of derived data. It is
covered by `.gitignore` (`simulation/networks/`, `*.net.xml`) and is regenerated
deterministically from the OSM extract with SUMO 1.27.1:

```sh
netconvert \
  --osm-files data/osm/bengaluru.osm.xml \
  --output-files simulation/networks/osm_urban_v1/osm.net.xml \
  --projection "+proj=utm +zone=43 +ellps=WGS84 +datum=WGS84 +units=m +no_defs" \
  --osm.ignore-extensions=false \
  --tls.guess \
  --junctions.corner-detail 5 \
  --junctions.join \
  --tls.join \
  --numerical-ids
```

Do **not** pass `--proj.plain-geo`. The network already carries a correct
UTM 43N projection, and plain-geo exists for output writers, not simulation.
Regenerating with it would change the projection the importer refuses to guess.

The importer reads `projParameter`, `netOffset` and `origBoundary` from the
network rather than assuming them, so the projection above is a convenience, not
a contract.

## Import into the database

Import is an explicit operator action. It is never run by Alembic, by
application startup, or by a test fixture:

```sh
cd backend
python scripts/import_road_network.py \
  --net simulation/networks/osm_urban_v1/osm.net.xml \
  --network-key osm_urban_v1
```

Re-running is safe. Existing edges for the key are deleted inside the same
transaction before the new set is written, so an edge dropped from the source
network cannot survive. `--verify` reports stored counts without writing.

Requires `alembic upgrade head` to have applied `0005_road_network`.

## Consuming it

* `app/services/road_graph.py` builds a `networkx.DiGraph` from `road_edges`,
  with node `x`/`y` in WGS84 and the five AI attributes on every edge.
* `GET /api/v1/road-networks/{key}` and `/{key}/edges` expose the same data as
  JSON and GeoJSON.
* `road_network_key` in `app/config.py` selects which imported network is used.
  An absent network leaves existing development behaviour untouched.