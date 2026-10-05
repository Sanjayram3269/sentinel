/**
 * Operational map.
 *
 * Three deliberate decisions:
 *
 * 1. The style URL is configurable and optional. No paid Mapbox token is
 *    hardcoded. When no style resolves, the map still renders as a dark
 *    operational grid rather than crashing or showing a grey void -- a demo
 *    where the map is blank looks broken even when the data is perfect.
 * 2. Roles have distinct, ordered visual weight. Primary is solid and heavy,
 *    backups quieter. If all three looked the same, the whole point of
 *    resilient routing is invisible.
 * 3. Geometry comes from the backend. Nothing here invents a route.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
  setWorkerUrl,
  type GeoJSONSource,
  type LngLatBoundsLike,
  type MapLayerMouseEvent,
  type StyleSpecification,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre resolves its web worker relative to its own `import.meta.url`. Once
// Vite bundles the library into `/assets/index-*.js`, that lookup points at a
// file that does not exist and tile/GeoJSON work never starts. Handing Vite the
// worker as a real asset and pointing MapLibre at it is the supported fix.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { Crosshair, Layers } from 'lucide-react'
import { ROLE_LABEL, ROLE_LINE, type LineRole } from '../../lib/status'
import type { ResilienceRole, RouteRead } from '../../types/api'

export interface MapMarker {
  id: string
  kind: 'incident' | 'vehicle' | 'hospital'
  longitude: number
  latitude: number
  label: string
  detail?: string
  live?: boolean
}

/** Minimal local GeoJSON shapes, so no extra @types package is needed. */
interface PointFeature {
  type: 'Feature'
  properties: Record<string, string | number>
  geometry: { type: 'Point'; coordinates: [number, number] }
}

interface LineFeature {
  type: 'Feature'
  properties: { id: string; role: LineRole; selected: boolean }
  geometry: { type: 'LineString'; coordinates: [number, number][] }
}

interface OperationalMapProps {
  routes: RouteRead[]
  /** Route id -> resilience role. Resolved from the backend resilience read. */
  roleByRouteId: Record<string, ResilienceRole>
  selectedRouteId: string | null
  onSelectRoute: (routeId: string) => void
  markers: MapMarker[]
  className?: string
}

// Must run before the first Map is constructed.
setWorkerUrl(maplibreWorkerUrl)

const STYLE_URL: string | undefined = import.meta.env['VITE_MAP_STYLE_URL'] as
  | string
  | undefined

/**
 * Offline basemap. A local style definition, not an external fetch, so the map
 * is never blank and never blocks on a network request.
 */
const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  name: 'sentinel-offline-operational',
  sources: {},
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#080b10' } },
  ],
}

/** Marker geometry per kind, drawn as GeoJSON so it needs no sprite. */
function markerFeatures(markers: MapMarker[]): { type: 'FeatureCollection'; features: PointFeature[] } {
  return {
    type: 'FeatureCollection',
    features: markers.map((marker) => ({
      type: 'Feature',
      properties: {
        id: marker.id,
        kind: marker.kind,
        label: marker.label,
        detail: marker.detail ?? '',
        live: marker.live ? 1 : 0,
      },
      geometry: { type: 'Point', coordinates: [marker.longitude, marker.latitude] },
    })),
  }
}

const MARKER_PAINT: Record<
  MapMarker['kind'],
  { color: string; radius: number; stroke: string }
> = {
  incident: { color: '#ef4444', radius: 8, stroke: '#7f1d1d' },
  vehicle: { color: '#f97316', radius: 7, stroke: '#7c2d12' },
  hospital: { color: '#22d3ee', radius: 7, stroke: '#155e75' },
}

const MARKER_KINDS = ['incident', 'vehicle', 'hospital'] as const

export function OperationalMap({
  routes,
  roleByRouteId,
  selectedRouteId,
  onSelectRoute,
  markers,
  className = '',
}: OperationalMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [ready, setReady] = useState(false)
  const [styleFailed, setStyleFailed] = useState(false)
  // Keeps the map's click handler current without rebuilding the map whenever
  // the parent passes a new callback identity.
  const onSelectRef = useRef(onSelectRoute)
  useEffect(() => {
    onSelectRef.current = onSelectRoute
  }, [onSelectRoute])

  // Role-resolved lines, ordered so the selected route is drawn last, on top.
  const lines = useMemo(() => {
    return routes
      .filter((route) => route.geometry.length > 1)
      .map((route) => {
        const role = (roleByRouteId[route.id] ?? 'ALTERNATE') as LineRole
        return {
          id: route.id,
          role,
          selected: route.id === selectedRouteId,
          coordinates: route.geometry.map(
            (point) => [point.longitude, point.latitude] as [number, number],
          ),
        }
      })
      .sort((a, b) => Number(a.selected) - Number(b.selected))
  }, [routes, roleByRouteId, selectedRouteId])

  // Mirror of `lines` for the fit effect, which must not re-run on every poll.
  const linesRef = useRef(lines)
  useEffect(() => {
    linesRef.current = lines
  }, [lines])

  const markerData = useMemo(() => markerFeatures(markers), [markers])

  // One-time map construction.
  useEffect(() => {
    const container = containerRef.current
    if (!container || mapRef.current) return

    const map = new MapLibreMap({
      container,
      style: STYLE_URL && STYLE_URL.trim() !== '' ? STYLE_URL : FALLBACK_STYLE,
      center: [77.582458, 13.093528],
      zoom: 14.2,
      attributionControl: { compact: true },
      // Offline basemap has nothing to rotate towards.
      dragRotate: Boolean(STYLE_URL),
      pitchWithRotate: false,
    })

    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new ScaleControl({ maxWidth: 90, unit: 'metric' }), 'bottom-left')

    // If a configured remote style fails, fall back rather than leaving the
    // operator with an empty canvas and no explanation.
    map.on('error', (event) => {
      if (event.error && !map.isStyleLoaded()) setStyleFailed(true)
    })

    map.on('load', () => {
      map.addSource('sentinel-routes', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })

      for (const key of Object.keys(ROLE_LINE) as LineRole[]) {
        map.addLayer({
          id: `route-${key}`,
          type: 'line',
          source: 'sentinel-routes',
          filter: ['==', ['get', 'role'], key],
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: {
            'line-color': ROLE_LINE[key].color,
            'line-width': ROLE_LINE[key].width,
            'line-opacity': ROLE_LINE[key].opacity,
            ...(ROLE_LINE[key].dash ? { 'line-dasharray': ROLE_LINE[key].dash } : {}),
          },
        })
      }

      // Halo under the selected line so it stays legible over any basemap.
      map.addLayer({
        id: 'route-halo',
        type: 'line',
        source: 'sentinel-routes',
        filter: ['==', ['get', 'selected'], true],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#ffffff', 'line-width': 11, 'line-opacity': 0.14 },
      })

      map.addSource('sentinel-markers', { type: 'geojson', data: markerData })
      for (const kind of MARKER_KINDS) {
        const spec = MARKER_PAINT[kind]
        map.addLayer({
          id: `marker-${kind}-halo`,
          type: 'circle',
          source: 'sentinel-markers',
          filter: ['==', ['get', 'kind'], kind],
          paint: {
            'circle-radius': spec.radius + 5,
            'circle-color': spec.color,
            'circle-opacity': 0.16,
          },
        })
        map.addLayer({
          id: `marker-${kind}`,
          type: 'circle',
          source: 'sentinel-markers',
          filter: ['==', ['get', 'kind'], kind],
          paint: {
            'circle-radius': spec.radius,
            'circle-color': spec.color,
            'circle-stroke-color': spec.stroke,
            'circle-stroke-width': 2,
          },
        })
      }

      const routeLayers: Array<`route-${LineRole}`> = [
        'route-PRIMARY',
        'route-BACKUP',
        'route-CONTINGENCY',
        'route-ALTERNATE',
      ]
      for (const layerId of routeLayers) {
        map.on('click', layerId, (event: MapLayerMouseEvent) => {
          const id = event.features?.[0]?.properties?.['id']
          if (typeof id === 'string') onSelectRef.current(id)
        })
        map.on('mouseenter', layerId, () => {
          map.getCanvas().style.cursor = 'pointer'
        })
        map.on('mouseleave', layerId, () => {
          map.getCanvas().style.cursor = ''
        })
      }

      setReady(true)
    })

    mapRef.current = map

    // MapLibre measures its container once at construction. In a flex command
    // layout the container usually has no final size yet, which leaves the
    // canvas at its 300px default and the route lines off-screen. Observing the
    // container and calling resize() is what keeps geometry visible.
    const observer = new ResizeObserver(() => map.resize())
    observer.observe(container)

    return () => {
      observer.disconnect()
      map.remove()
      mapRef.current = null
    }
  }, [markerData])

  // Push route geometry whenever it changes.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const source = map.getSource('sentinel-routes') as GeoJSONSource | undefined
    if (!source) return
    const features: LineFeature[] = lines.map((line) => ({
      type: 'Feature',
      properties: { id: line.id, role: line.role, selected: line.selected },
      geometry: { type: 'LineString', coordinates: line.coordinates },
    }))
    source.setData({ type: 'FeatureCollection', features })
  }, [lines, ready])

  // Push markers.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const source = map.getSource('sentinel-markers') as GeoJSONSource | undefined
    if (!source) return
    source.setData(markerData)
  }, [markerData, ready])

  // Fit the view to whatever route geometry exists.
  //
  // Keyed on a content hash rather than on `lines` identity: a 15s poll that
  // refetches identical geometry must not yank the operator's viewport, but a
  // genuinely different selection must re-fit.
  const geometryKey = useMemo(
    () => `${selectedRouteId ?? ''}:${lines.length}:${lines[0]?.id ?? ''}`,
    [selectedRouteId, lines],
  )
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const withGeometry = linesRef.current.filter((line) => line.coordinates.length > 1)
    if (withGeometry.length === 0) return
    const bounds = new LngLatBounds()
    for (const line of withGeometry) {
      for (const coordinate of line.coordinates) bounds.extend(coordinate)
    }
    if (bounds.isEmpty()) return
    map.fitBounds(bounds as LngLatBoundsLike, {
      padding: { top: 60, bottom: 60, left: 60, right: 60 },
      duration: 700,
      maxZoom: 16.5,
    })
  }, [ready, geometryKey, linesRef])

  const recentre = () => {
    const map = mapRef.current
    if (!map) return
    map.flyTo({ center: [77.582458, 13.093528], zoom: 14.6, duration: 600 })
  }

  return (
    <div className={`relative min-h-0 flex-1 overflow-hidden ${className}`}>
      {/* MapLibre's own stylesheet forces `position: relative` on its
          container, so an `absolute inset-0` overlay trick collapses to zero
          height. Explicit full size is the reliable way to fill the panel. */}
      <div ref={containerRef} className="h-full w-full" aria-label="Operational map" />

      {/* Operational grid underlay so the offline basemap still reads as a map. */}
      <div aria-hidden="true" className="grid-scan pointer-events-none absolute inset-0 opacity-60" />

      <div className="pointer-events-none absolute left-3 top-3 z-10 flex flex-col gap-2">
        <MapLegend />
      </div>

      <div className="absolute right-3 top-3 z-10 flex flex-col gap-1.5">
        <button
          type="button"
          onClick={recentre}
          aria-label="Recentre map on incident"
          title="Recentre on incident"
          className="pointer-events-auto rounded border border-graphite-600 bg-graphite-850/90 p-1.5 text-ink-300 transition-colors hover:border-intel-500 hover:text-intel-300"
        >
          <Crosshair size={14} />
        </button>
      </div>

      {styleFailed && (
        <div className="absolute bottom-3 right-3 z-10">
          <span className="inline-flex items-center gap-1 rounded border border-graphite-600 bg-graphite-850/90 px-2 py-1 text-[10px] uppercase tracking-[0.08em] text-ink-400">
            <Layers size={11} /> Offline basemap
          </span>
        </div>
      )}
    </div>
  )
}

function MapLegend() {
  const items: Array<{ role: LineRole; label: string }> = [
    { role: 'PRIMARY', label: ROLE_LABEL.PRIMARY },
    { role: 'BACKUP', label: ROLE_LABEL.BACKUP },
    { role: 'CONTINGENCY', label: ROLE_LABEL.CONTINGENCY },
  ]
  return (
    <div className="pointer-events-auto rounded border border-graphite-700 bg-graphite-900/85 px-2.5 py-2 backdrop-blur">
      <p className="label-caps mb-1.5">Legend</p>
      <ul className="flex flex-col gap-1">
        {items.map((item) => (
          <li key={item.role} className="flex items-center gap-2">
            <svg width="22" height="6" aria-hidden="true" className="shrink-0">
              <line
                x1="0"
                y1="3"
                x2="22"
                y2="3"
                stroke={ROLE_LINE[item.role].color}
                strokeWidth={ROLE_LINE[item.role].width}
                strokeDasharray={ROLE_LINE[item.role].dash?.join(' ')}
                opacity={ROLE_LINE[item.role].opacity}
              />
            </svg>
            <span className="text-[10px] font-semibold tracking-[0.06em] text-ink-300">
              {item.label}
            </span>
          </li>
        ))}
        <li className="mt-0.5 flex items-center gap-2 border-t border-graphite-700 pt-1">
          <span className="h-2 w-2 shrink-0 rounded-full bg-alert-400" />
          <span className="text-[10px] tracking-[0.06em] text-ink-400">Incident</span>
        </li>
        <li className="flex items-center gap-2">
          <span className="h-2 w-2 shrink-0 rounded-full bg-emergency-400" />
          <span className="text-[10px] tracking-[0.06em] text-ink-400">Ambulance</span>
        </li>
        <li className="flex items-center gap-2">
          <span className="h-2 w-2 shrink-0 rounded-full bg-intel-400" />
          <span className="text-[10px] tracking-[0.06em] text-ink-400">Hospital</span>
        </li>
      </ul>
    </div>
  )
}
