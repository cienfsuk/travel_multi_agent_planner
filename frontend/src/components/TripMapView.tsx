import {
  useEffect,
  useRef,
  useImperativeHandle,
  forwardRef,
  useState,
} from "react";
import type { AnimationBundle } from "../types/api";
import { fetchConfig } from "../api/client";

declare global {
  interface Window {
    TMap: any;
  }
}

const DAY_COLORS = [
  "#2563eb",
  "#f97316",
  "#0f766e",
  "#7c3aed",
  "#dc2626",
  "#0891b2",
  "#84cc16",
];
const KIND_COLORS: Record<string, string> = {
  hotel: "#c2410c",
  spot: "#2563eb",
  lunch: "#16a34a",
  dinner: "#dc2626",
  food: "#16a34a",
};

function dayColor(day: number): string {
  return DAY_COLORS[(Math.max(1, day) - 1) % DAY_COLORS.length];
}
function nodeTypeColor(kind: string): string {
  return KIND_COLORS[kind] || "#475569";
}

function markerSvg(label: string, fill: string, outline: string): string {
  return (
    "data:image/svg+xml;charset=UTF-8," +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="60" viewBox="0 0 48 60">` +
        `<path d="M24 4c10.9 0 19.8 8.9 19.8 19.8 0 13.9-15.2 26.8-19.8 32.1C19.4 50.6 4.2 37.7 4.2 23.8 4.2 12.9 13.1 4 24 4z" fill="${fill}" stroke="${outline}" stroke-width="2"/>` +
        `<circle cx="24" cy="24" r="11" fill="rgba(255,255,255,.18)"/>` +
        `<text x="24" y="29" font-size="16" text-anchor="middle" fill="#fff" font-family="Microsoft YaHei,sans-serif" font-weight="700">${label}</text>` +
        `</svg>`,
    )
  );
}

function highlightSvg(fill: string): string {
  return (
    "data:image/svg+xml;charset=UTF-8," +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="56" height="72" viewBox="0 0 56 72">` +
        `<circle cx="28" cy="29" r="12" fill="${fill}" opacity=".16">` +
        `<animate attributeName="r" values="12;21;12" dur="1.4s" repeatCount="indefinite"/>` +
        `<animate attributeName="opacity" values=".3;.04;.3" dur="1.4s" repeatCount="indefinite"/>` +
        `</circle>` +
        `<path d="M28 4c12.2 0 22 9.8 22 22 0 15.8-17 30.5-22 36-5-5.5-22-20.2-22-36 0-12.2 9.8-22 22-22z" fill="${fill}" stroke="#fff" stroke-width="2.5"/>` +
        `<circle cx="28" cy="26" r="9" fill="#fff" opacity=".96"/>` +
        `</svg>`,
    )
  );
}

// Singleton SDK loader
let _sdkState: "idle" | "loading" | "ready" = "idle";
const _sdkWaiters: Array<() => void> = [];

function loadTencentSDK(key: string): Promise<void> {
  return new Promise((resolve) => {
    if (_sdkState === "ready" && window.TMap) {
      resolve();
      return;
    }
    _sdkWaiters.push(resolve);
    if (_sdkState === "loading") return;
    _sdkState = "loading";
    const script = document.createElement("script");
    script.src = `https://map.qq.com/api/gljs?v=1.exp&key=${encodeURIComponent(key)}`;
    script.onload = () => {
      _sdkState = "ready";
      _sdkWaiters.splice(0).forEach((cb) => cb());
    };
    script.onerror = () => {
      _sdkState = "idle";
      _sdkWaiters.splice(0);
    };
    document.head.appendChild(script);
  });
}

function applyGeometries(layer: any, geometries: any[]) {
  if (!layer) return;
  try {
    if (typeof layer.setGeometries === "function") {
      layer.setGeometries(geometries);
      return;
    }
  } catch (_) {
    /* ignore */
  }
  try {
    if (typeof layer.updateGeometries === "function")
      layer.updateGeometries(geometries);
  } catch (_) {
    /* ignore */
  }
}

export interface TripMapHandle {
  flyTo(lat: number, lon: number, day: number, stepIndex: number): void;
}

interface Props {
  animation: AnimationBundle | null;
  selectedDay?: number | null;
  activeStepKey?: { day: number; stepIndex: number } | null;
}

const TripMapView = forwardRef<TripMapHandle, Props>(
  ({ animation, selectedDay, activeStepKey }, ref) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<any>(null);
    const segLayerRef = useRef<any>(null);
    const nodeLayerRef = useRef<any>(null);
    const hlLayerRef = useRef<any>(null);
    const [mapReady, setMapReady] = useState(false);

    // Load SDK and init map on mount
    useEffect(() => {
      let cancelled = false;

      fetchConfig()
        .then(({ tencent_map_js_key }) => {
          if (cancelled || !tencent_map_js_key) return;
          return loadTencentSDK(tencent_map_js_key);
        })
        .then(() => {
          if (
            cancelled ||
            !containerRef.current ||
            mapRef.current ||
            !window.TMap
          )
            return;
          const TMap = window.TMap;

          const map = new TMap.Map(containerRef.current, {
            center: new TMap.LatLng(31.23, 121.47),
            zoom: 12,
            pitch: 42,
            rotation: 0,
          });
          mapRef.current = map;

          segLayerRef.current = new TMap.MultiPolyline({
            map,
            styles: {},
            geometries: [],
          });
          nodeLayerRef.current = new TMap.MultiMarker({
            map,
            styles: {},
            geometries: [],
          });
          hlLayerRef.current = new TMap.MultiMarker({
            map,
            styles: {},
            geometries: [],
          });

          setMapReady(true);
        })
        .catch(console.error);

      return () => {
        cancelled = true;
        try {
          mapRef.current?.destroy?.();
        } catch (_) {
          /* ignore */
        }
        mapRef.current = null;
        setMapReady(false);
      };
    }, []);

    // Draw animation whenever map or data changes
    useEffect(() => {
      if (!mapReady || !animation || !window.TMap) return;
      const TMap = window.TMap;
      const nodes =
        selectedDay != null
          ? animation.nodes.filter((n) => n.day === selectedDay)
          : animation.nodes;
      const segments =
        selectedDay != null
          ? animation.segments.filter((s) => s.day === selectedDay)
          : animation.segments;

      // --- Polylines ---
      const segStyles: Record<string, any> = {};
      const segGeoms: any[] = [];
      segments
        .filter((s) => s.path && s.path.length >= 2)
        .forEach((seg, idx) => {
          const id = `seg-${idx}`;
          segStyles[id] = new TMap.PolylineStyle({
            color: seg.color || "#6366f1",
            width: 7,
            borderWidth: 0,
            lineCap: "round",
          });
          segGeoms.push({
            id,
            styleId: id,
            paths: seg.path.map(
              ([lon, lat]: [number, number]) => new TMap.LatLng(lat, lon),
            ),
          });
        });
      if (segLayerRef.current) {
        if (typeof segLayerRef.current.setStyles === "function")
          segLayerRef.current.setStyles(segStyles);
        applyGeometries(segLayerRef.current, segGeoms);
      }

      // --- Markers ---
      const markerStyles: Record<string, any> = {};
      const markerGeoms: any[] = [];
      nodes.forEach((node, idx) => {
        const fill = node.type_color || nodeTypeColor(node.kind);
        const outline = node.day_color || dayColor(node.day);
        const id = `node-${idx}`;
        markerStyles[id] = new TMap.MarkerStyle({
          width: 38,
          height: 48,
          anchor: { x: 19, y: 44 },
          src: markerSvg(node.marker_text, fill, outline),
        });
        markerGeoms.push({
          id: `node-${node.day}-${node.step_index}`,
          styleId: id,
          position: new TMap.LatLng(node.lat, node.lon),
        });
      });
      if (nodeLayerRef.current) {
        if (typeof nodeLayerRef.current.setStyles === "function")
          nodeLayerRef.current.setStyles(markerStyles);
        applyGeometries(nodeLayerRef.current, markerGeoms);
      }

      // Highlight active step node
      if (activeStepKey && hlLayerRef.current) {
        const activeNode = nodes.find(
          (n) =>
            n.day === activeStepKey.day &&
            n.step_index === activeStepKey.stepIndex,
        );
        if (activeNode) {
          const fill = activeNode.type_color || nodeTypeColor(activeNode.kind);
          if (typeof hlLayerRef.current.setStyles === "function") {
            hlLayerRef.current.setStyles({
              hl: new TMap.MarkerStyle({
                width: 54,
                height: 68,
                anchor: { x: 27, y: 60 },
                src: highlightSvg(fill),
              }),
            });
          }
          applyGeometries(hlLayerRef.current, [
            {
              id: "current",
              styleId: "hl",
              position: new TMap.LatLng(activeNode.lat, activeNode.lon),
            },
          ]);
        } else {
          applyGeometries(hlLayerRef.current, []);
        }
      } else {
        applyGeometries(hlLayerRef.current, []);
      }

      // Fit bounds
      if (nodes.length > 0) {
        const lats = nodes.map((n) => n.lat);
        const lons = nodes.map((n) => n.lon);
        const bounds = new TMap.LatLngBounds(
          new TMap.LatLng(Math.min(...lats), Math.min(...lons)),
          new TMap.LatLng(Math.max(...lats), Math.max(...lons)),
        );
        mapRef.current?.fitBounds(bounds, { padding: 70 });
      }
    }, [animation, mapReady, selectedDay, activeStepKey]);

    // Expose flyTo
    useImperativeHandle(
      ref,
      () => ({
        flyTo(lat: number, lon: number, day: number, stepIndex: number) {
          const TMap = window.TMap;
          const map = mapRef.current;
          if (!map || !TMap) return;

          const node = animation?.nodes.find(
            (n) => n.day === day && n.step_index === stepIndex,
          );
          const fill = node?.type_color || nodeTypeColor(node?.kind ?? "spot");

          // Update highlight layer
          if (hlLayerRef.current) {
            if (typeof hlLayerRef.current.setStyles === "function") {
              hlLayerRef.current.setStyles({
                hl: new TMap.MarkerStyle({
                  width: 54,
                  height: 68,
                  anchor: { x: 27, y: 60 },
                  src: highlightSvg(fill),
                }),
              });
            }
            applyGeometries(hlLayerRef.current, [
              {
                id: "current",
                styleId: "hl",
                position: new TMap.LatLng(lat, lon),
              },
            ]);
          }

          // Smooth animated pan + zoom
          const targetZoom = 15;
          if (typeof map.easeTo === "function") {
            // TMap GL JS native smooth transition
            map.easeTo({
              center: new TMap.LatLng(lat, lon),
              zoom: targetZoom,
              duration: 700,
            });
          } else {
            // Fallback: manual rAF lerp over 700ms
            const startCenter =
              typeof map.getCenter === "function" ? map.getCenter() : null;
            const startZoom =
              typeof map.getZoom === "function" ? map.getZoom() : targetZoom;
            const startLat = startCenter ? startCenter.getLat() : lat;
            const startLon = startCenter ? startCenter.getLng() : lon;
            const duration = 700;
            const t0 = performance.now();
            const easeInOut = (t: number) =>
              t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
            const tick = (now: number) => {
              const p = easeInOut(Math.min((now - t0) / duration, 1));
              if (typeof map.setCenter === "function")
                map.setCenter(
                  new TMap.LatLng(
                    startLat + (lat - startLat) * p,
                    startLon + (lon - startLon) * p,
                  ),
                );
              if (typeof map.setZoom === "function")
                map.setZoom(startZoom + (targetZoom - startZoom) * p);
              if (p < 1) requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
          }
        },
      }),
      [animation, mapReady],
    );

    return (
      <div
        ref={containerRef}
        className="w-full h-full rounded-2xl overflow-hidden"
      />
    );
  },
);

TripMapView.displayName = "TripMapView";
export default TripMapView;
