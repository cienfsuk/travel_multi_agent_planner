import {
  useEffect,
  useRef,
  useImperativeHandle,
  forwardRef,
  useState,
} from "react";
import type { AnimationBundle, RoutePlanLeg } from "../types/api";
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

function travelerSvg(fill: string): string {
  return (
    "data:image/svg+xml;charset=UTF-8," +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 44 44">` +
        `<circle cx="22" cy="22" r="16" fill="${fill}" opacity=".92" />` +
        `<path d="M16 21h9.5l-2.8-2.8 1.6-1.6 5.5 5.4-5.5 5.5-1.6-1.6 2.8-2.9H16z" fill="#fff"/>` +
        `</svg>`,
    )
  );
}

let sdkState: "idle" | "loading" | "ready" = "idle";
const sdkWaiters: Array<() => void> = [];

function loadTencentSDK(key: string): Promise<void> {
  return new Promise((resolve) => {
    if (sdkState === "ready" && window.TMap) {
      resolve();
      return;
    }
    sdkWaiters.push(resolve);
    if (sdkState === "loading") {
      return;
    }
    sdkState = "loading";
    const script = document.createElement("script");
    script.src = `https://map.qq.com/api/gljs?v=1.exp&key=${encodeURIComponent(key)}`;
    script.onload = () => {
      sdkState = "ready";
      sdkWaiters.splice(0).forEach((cb) => cb());
    };
    script.onerror = () => {
      sdkState = "idle";
      sdkWaiters.splice(0);
    };
    document.head.appendChild(script);
  });
}

function applyGeometries(layer: any, geometries: any[]) {
  if (!layer) {
    return;
  }
  try {
    if (typeof layer.setGeometries === "function") {
      layer.setGeometries(geometries);
      return;
    }
  } catch (_) {
    /* ignore */
  }
  try {
    if (typeof layer.updateGeometries === "function") {
      layer.updateGeometries(geometries);
    }
  } catch (_) {
    /* ignore */
  }
}

function compareStepKey(
  dayA: number,
  stepIndexA: number,
  dayB: number,
  stepIndexB: number,
): number {
  if (dayA !== dayB) {
    return dayA - dayB;
  }
  return stepIndexA - stepIndexB;
}

function segmentDistanceMeters(
  start: [number, number],
  end: [number, number],
): number {
  const toRad = (value: number) => (value * Math.PI) / 180;
  const lat1 = toRad(start[1]);
  const lat2 = toRad(end[1]);
  const dLat = lat2 - lat1;
  const dLon = toRad(end[0] - start[0]);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1) *
      Math.cos(lat2) *
      Math.sin(dLon / 2) *
      Math.sin(dLon / 2);
  return 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function pathDistanceKm(path: [number, number][]): number {
  if (!Array.isArray(path) || path.length < 2) {
    return 0;
  }
  let meters = 0;
  for (let i = 1; i < path.length; i += 1) {
    meters += segmentDistanceMeters(path[i - 1], path[i]);
  }
  return meters / 1000;
}

function locatePathProgress(
  path: [number, number][],
  progress: number,
): { index: number; point: [number, number] } | null {
  if (!Array.isArray(path) || path.length < 2) {
    return null;
  }
  const clamped = Math.max(0, Math.min(1, progress));
  if (clamped <= 0) {
    return { index: 0, point: [path[0][0], path[0][1]] };
  }
  if (clamped >= 1) {
    const last = path[path.length - 1];
    return { index: path.length - 2, point: [last[0], last[1]] };
  }

  const cumulative: number[] = [0];
  for (let i = 1; i < path.length; i += 1) {
    const dist = Math.max(0, segmentDistanceMeters(path[i - 1], path[i]));
    cumulative.push(cumulative[i - 1] + dist);
  }
  const total = cumulative[cumulative.length - 1];
  if (total <= 0) {
    return { index: 0, point: [path[0][0], path[0][1]] };
  }

  const target = total * clamped;
  for (let i = 1; i < cumulative.length; i += 1) {
    if (target > cumulative[i]) {
      continue;
    }
    const prevTotal = cumulative[i - 1];
    const segTotal = cumulative[i] - prevTotal;
    const ratio = segTotal <= 0 ? 1 : (target - prevTotal) / segTotal;
    const start = path[i - 1];
    const end = path[i];
    return {
      index: i - 1,
      point: [
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio,
      ],
    };
  }

  const last = path[path.length - 1];
  return { index: path.length - 2, point: [last[0], last[1]] };
}

function partialPath(
  path: [number, number][],
  progress: number,
): [number, number][] {
  if (!Array.isArray(path) || path.length < 2) {
    return path || [];
  }
  const located = locatePathProgress(path, progress);
  if (!located) {
    return path;
  }
  const visible = path.slice(0, located.index + 1);
  visible.push(located.point);
  return visible;
}

function remainingPath(
  path: [number, number][],
  progress: number,
): [number, number][] {
  if (!Array.isArray(path) || path.length < 2) {
    return [];
  }
  const located = locatePathProgress(path, progress);
  if (!located) {
    return path;
  }
  const remain: [number, number][] = [located.point];
  remain.push(...path.slice(located.index + 1));
  return remain.length >= 2 ? remain : path.slice(-2);
}

function currentPosition(
  path: [number, number][],
  progress: number,
): [number, number] | null {
  const located = locatePathProgress(path, progress);
  return located ? located.point : null;
}

function isValidLonLat(lon: number, lat: number): boolean {
  return (
    Number.isFinite(lon) &&
    Number.isFinite(lat) &&
    lon >= -180 &&
    lon <= 180 &&
    lat >= -90 &&
    lat <= 90
  );
}

function isLocallyContinuous(
  prev: [number, number],
  next: [number, number],
): boolean {
  return (
    Math.abs(prev[0] - next[0]) <= 1.5 && Math.abs(prev[1] - next[1]) <= 1.5
  );
}

function normalizeLonLatPoint(
  point: [number, number],
): [number, number] | null {
  const x = Number(point[0]);
  const y = Number(point[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }
  if (isValidLonLat(x, y)) {
    return [x, y];
  }
  return null;
}

function normalizePathPoint(
  point: [number, number],
  prev: [number, number] | null,
): [number, number] | null {
  const direct = normalizeLonLatPoint(point);
  if (direct && (!prev || isLocallyContinuous(prev, direct))) {
    return direct;
  }
  const x = Number(point[0]);
  const y = Number(point[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }

  if (prev && Math.abs(x) <= 1_000_000 && Math.abs(y) <= 1_000_000) {
    const candidate: [number, number] = [
      prev[0] + x / 1_000_000,
      prev[1] + y / 1_000_000,
    ];
    if (
      isValidLonLat(candidate[0], candidate[1]) &&
      isLocallyContinuous(prev, candidate)
    ) {
      return candidate;
    }
  }

  if (isValidLonLat(y, x)) {
    const swapped: [number, number] = [y, x];
    if (!prev || isLocallyContinuous(prev, swapped)) {
      return swapped;
    }
  }
  return null;
}

function sanitizePath(path: [number, number][]): [number, number][] {
  if (!Array.isArray(path)) {
    return [];
  }
  const cleaned: [number, number][] = [];
  for (const point of path) {
    if (!Array.isArray(point) || point.length < 2) {
      continue;
    }
    const prev = cleaned[cleaned.length - 1] ?? null;
    const normalized = normalizePathPoint([point[0], point[1]], prev);
    if (!normalized) {
      continue;
    }
    if (!prev || prev[0] !== normalized[0] || prev[1] !== normalized[1]) {
      cleaned.push(normalized);
    }
  }
  return cleaned;
}

function toLatLng(TMap: any, lonRaw: number, latRaw: number): any | null {
  const normalized = normalizeLonLatPoint([lonRaw, latRaw]);
  if (!normalized) {
    return null;
  }
  return new TMap.LatLng(normalized[1], normalized[0]);
}

function callLayerMethod(layer: any, methods: string[]): boolean {
  for (const method of methods) {
    try {
      if (typeof layer?.[method] === "function") {
        layer[method]();
        return true;
      }
    } catch (_) {
      /* ignore */
    }
  }
  return false;
}

function stopMarkerMove(layer: any) {
  callLayerMethod(layer, ["stopMove", "stopMoving", "clearMove", "stop"]);
}

function pauseMarkerMove(layer: any) {
  const paused = callLayerMethod(layer, ["pauseMove"]);
  if (!paused) {
    stopMarkerMove(layer);
  }
}

export interface TripMapHandle {
  flyTo(lat: number, lon: number, day: number, stepIndex: number): void;
}

interface Props {
  animation: AnimationBundle | null;
  selectedDay?: number | null;
  activeStepKey?: { day: number; stepIndex: number } | null;
  showRoutes?: boolean;
  stepProgress?: number;
  routeLegs?: RoutePlanLeg[] | null;
  activeLegIndex?: number;
  isPlaying?: boolean;
  activeLegDurationMs?: number;
}

const TripMapView = forwardRef<TripMapHandle, Props>(
  (
    {
      animation,
      selectedDay,
      activeStepKey,
      showRoutes = false,
      stepProgress = 0,
      routeLegs,
      activeLegIndex = 0,
      isPlaying = false,
      activeLegDurationMs = 2200,
    },
    ref,
  ) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<any>(null);
    const segLayerRef = useRef<any>(null);
    const nodeLayerRef = useRef<any>(null);
    const hlLayerRef = useRef<any>(null);
    const movingLayerRef = useRef<any>(null);
    const lastFitKeyRef = useRef("");
    const movingRunKeyRef = useRef("");
    const [mapReady, setMapReady] = useState(false);

    useEffect(() => {
      let cancelled = false;

      fetchConfig()
        .then(({ tencent_map_js_key }) => {
          if (cancelled || !tencent_map_js_key) {
            return;
          }
          return loadTencentSDK(tencent_map_js_key);
        })
        .then(() => {
          if (
            cancelled ||
            !containerRef.current ||
            mapRef.current ||
            !window.TMap
          ) {
            return;
          }
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
          movingLayerRef.current = new TMap.MultiMarker({
            map,
            styles: {
              traveler: new TMap.MarkerStyle({
                width: 40,
                height: 40,
                anchor: { x: 20, y: 20 },
                src: travelerSvg("#f59e0b"),
              }),
            },
            geometries: [],
          });

          setMapReady(true);
        })
        .catch(console.error);

      return () => {
        cancelled = true;
        try {
          stopMarkerMove(movingLayerRef.current);
          mapRef.current?.destroy?.();
        } catch (_) {
          /* ignore */
        }
        mapRef.current = null;
        setMapReady(false);
      };
    }, []);

    useEffect(() => {
      const container = containerRef.current;
      if (!container) {
        return;
      }
      const preventWheel = (event: WheelEvent) => {
        event.preventDefault();
      };
      const preventGesture = (event: Event) => {
        event.preventDefault();
      };
      container.addEventListener("wheel", preventWheel, { passive: false });
      container.addEventListener("dblclick", preventGesture, { passive: false });
      container.addEventListener("gesturestart", preventGesture, {
        passive: false,
      });
      container.addEventListener("gesturechange", preventGesture, {
        passive: false,
      });
      return () => {
        container.removeEventListener("wheel", preventWheel);
        container.removeEventListener("dblclick", preventGesture);
        container.removeEventListener("gesturestart", preventGesture);
        container.removeEventListener("gesturechange", preventGesture);
      };
    }, []);

    useEffect(() => {
      if (!mapReady || !animation || !window.TMap) {
        return;
      }
      const TMap = window.TMap;
      const nodes =
        selectedDay != null
          ? animation.nodes.filter((node) => node.day === selectedDay)
          : animation.nodes;
      const animSegments =
        selectedDay != null
          ? animation.segments.filter((segment) => segment.day === selectedDay)
          : animation.segments;

      const normalizedProgress = Math.max(0, Math.min(1, stepProgress));
      const activeDay = activeStepKey?.day;
      const activeStepIndex = activeStepKey?.stepIndex;
      const hasRouteLegs = Array.isArray(routeLegs) && routeLegs.length > 0;

      const segStyles: Record<string, any> = {};
      const segGeoms: any[] = [];
      if (showRoutes) {
        if (hasRouteLegs) {
          routeLegs.forEach((leg, index) => {
            if (index > activeLegIndex) {
              return;
            }
            const safePath = sanitizePath(leg.path);
            if (safePath.length < 2) {
              return;
            }
            const progress = index < activeLegIndex ? 1 : normalizedProgress;
            const clippedPath = partialPath(safePath, progress);
            if (clippedPath.length < 2) {
              return;
            }
            const latLngPath = clippedPath
              .map(([lon, lat]) => toLatLng(TMap, lon, lat))
              .filter(Boolean);
            if (latLngPath.length < 2) {
              return;
            }
            const id = `route-leg-${index}`;
            segStyles[id] = new TMap.PolylineStyle({
              color: dayColor(index + 1),
              width: 7,
              borderWidth: 0,
              lineCap: "round",
            });
            segGeoms.push({
              id,
              styleId: id,
              paths: latLngPath,
            });
          });
        } else if (activeDay != null && activeStepIndex != null) {
          animSegments
            .filter(
              (segment) =>
                Array.isArray(segment.path) && segment.path.length >= 2,
            )
            .forEach((segment, index) => {
              const safePath = sanitizePath(segment.path);
              if (safePath.length < 2) {
                return;
              }
              const order = compareStepKey(
                segment.day,
                segment.step_index,
                activeDay,
                activeStepIndex,
              );
              let progress = 0;
              if (order < 0) {
                progress = 1;
              } else if (order === 0) {
                progress = normalizedProgress;
              }
              if (order > 0) {
                return;
              }
              const clippedPath = partialPath(safePath, progress);
              if (clippedPath.length < 2) {
                return;
              }
              const latLngPath = clippedPath
                .map(([lon, lat]) => toLatLng(TMap, lon, lat))
                .filter(Boolean);
              if (latLngPath.length < 2) {
                return;
              }
              const id = `seg-${index}`;
              segStyles[id] = new TMap.PolylineStyle({
                color: segment.color || "#6366f1",
                width: 7,
                borderWidth: 0,
                lineCap: "round",
              });
              segGeoms.push({
                id,
                styleId: id,
                paths: latLngPath,
              });
            });
        }
      }

      if (segLayerRef.current) {
        if (typeof segLayerRef.current.setStyles === "function") {
          segLayerRef.current.setStyles(segStyles);
        }
        applyGeometries(segLayerRef.current, segGeoms);
      }

      const markerStyles: Record<string, any> = {};
      const markerGeoms: any[] = [];
      nodes.forEach((node, index) => {
        const nodeLatLng = toLatLng(TMap, node.lon, node.lat);
        if (!nodeLatLng) {
          return;
        }
        const fill = node.type_color || nodeTypeColor(node.kind);
        const outline = node.day_color || dayColor(node.day);
        const id = `node-${index}`;
        markerStyles[id] = new TMap.MarkerStyle({
          width: 38,
          height: 48,
          anchor: { x: 19, y: 44 },
          src: markerSvg(node.marker_text, fill, outline),
        });
        markerGeoms.push({
          id: `node-${node.day}-${node.step_index}`,
          styleId: id,
          position: nodeLatLng,
        });
      });
      if (nodeLayerRef.current) {
        if (typeof nodeLayerRef.current.setStyles === "function") {
          nodeLayerRef.current.setStyles(markerStyles);
        }
        applyGeometries(nodeLayerRef.current, markerGeoms);
      }

      const shouldHideHighlight = hasRouteLegs && isPlaying && showRoutes;
      if (shouldHideHighlight) {
        applyGeometries(hlLayerRef.current, []);
        return;
      }

      if (activeStepKey && hlLayerRef.current) {
        const activeNode = nodes.find(
          (node) =>
            node.day === activeStepKey.day &&
            node.step_index === activeStepKey.stepIndex,
        );
        let movingPoint: [number, number] | null = null;
        if (hasRouteLegs) {
          const legPath = sanitizePath(routeLegs[activeLegIndex]?.path ?? []);
          if (showRoutes && legPath.length >= 2) {
            movingPoint = currentPosition(legPath, normalizedProgress);
          }
        } else if (activeDay != null && activeStepIndex != null) {
          const currentSegment = animSegments.find(
            (segment) =>
              segment.day === activeDay &&
              segment.step_index === activeStepIndex &&
              Array.isArray(segment.path) &&
              segment.path.length >= 2,
          );
          const currentSegmentPath = currentSegment
            ? sanitizePath(currentSegment.path)
            : [];
          if (showRoutes && currentSegmentPath.length >= 2) {
            movingPoint = currentPosition(currentSegmentPath, normalizedProgress);
          }
        }

        const markerLon = movingPoint?.[0] ?? activeNode?.lon;
        const markerLat = movingPoint?.[1] ?? activeNode?.lat;
        const markerLatLng =
          typeof markerLon === "number" && typeof markerLat === "number"
            ? toLatLng(TMap, markerLon, markerLat)
            : null;

        if (activeNode && markerLatLng) {
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
              position: markerLatLng,
            },
          ]);
        } else {
          applyGeometries(hlLayerRef.current, []);
        }
      } else {
        applyGeometries(hlLayerRef.current, []);
      }
    }, [
      animation,
      mapReady,
      selectedDay,
      activeStepKey,
      showRoutes,
      stepProgress,
      routeLegs,
      activeLegIndex,
      isPlaying,
    ]);

    useEffect(() => {
      if (!mapReady || !window.TMap || !movingLayerRef.current) {
        return;
      }
      const TMap = window.TMap;
      const layer = movingLayerRef.current;
      const hasRouteLegs = Array.isArray(routeLegs) && routeLegs.length > 0;

      if (
        !hasRouteLegs ||
        !showRoutes ||
        activeLegIndex < 0 ||
        activeLegIndex >= routeLegs.length
      ) {
        stopMarkerMove(layer);
        applyGeometries(layer, []);
        movingRunKeyRef.current = "";
        return;
      }

      if (!isPlaying) {
        pauseMarkerMove(layer);
        movingRunKeyRef.current = "";
        return;
      }

      const runKey = `${activeLegIndex}:${activeLegDurationMs}`;
      if (movingRunKeyRef.current === runKey) {
        return;
      }
      movingRunKeyRef.current = runKey;

      const progress = Math.max(0, Math.min(1, stepProgress));
      const legPath = sanitizePath(routeLegs[activeLegIndex].path);
      const remainPath = remainingPath(legPath, progress);
      if (remainPath.length < 2) {
        return;
      }
      const latLngPath = remainPath
        .map(([lon, lat]) => toLatLng(TMap, lon, lat))
        .filter(Boolean);
      if (latLngPath.length < 2) {
        return;
      }
      if (typeof layer.setStyles === "function") {
        layer.setStyles({
          traveler: new TMap.MarkerStyle({
            width: 40,
            height: 40,
            anchor: { x: 20, y: 20 },
            src: travelerSvg("#f59e0b"),
          }),
        });
      }
      applyGeometries(layer, [
        {
          id: "traveler",
          styleId: "traveler",
          position: latLngPath[0],
        },
      ]);
      stopMarkerMove(layer);

      const remainingDurationMs = Math.max(
        900,
        Math.round(activeLegDurationMs * (1 - progress)),
      );
      const remainingDistanceKm = Math.max(0.02, pathDistanceKm(remainPath));
      const speedKmh = Math.max(
        4,
        Math.min(110, remainingDistanceKm / (remainingDurationMs / 3_600_000)),
      );

      try {
        if (typeof layer.moveAlong === "function") {
          layer.moveAlong(
            {
              traveler: {
                path: latLngPath,
                speed: speedKmh,
              },
            },
            { autoRotation: true },
          );
        }
      } catch (_) {
        /* ignore */
      }
    }, [
      mapReady,
      routeLegs,
      activeLegIndex,
      isPlaying,
      showRoutes,
      activeLegDurationMs,
      stepProgress,
    ]);

    useEffect(() => {
      if (!mapReady || !animation || !window.TMap) {
        return;
      }
      const TMap = window.TMap;
      const nodes =
        selectedDay != null
          ? animation.nodes.filter((node) => node.day === selectedDay)
          : animation.nodes;
      const validNodes = nodes
        .map((node) => {
          const normalized = normalizeLonLatPoint([node.lon, node.lat]);
          return normalized
            ? { node, lon: normalized[0], lat: normalized[1] }
            : null;
        })
        .filter(Boolean) as Array<{
        node: (typeof nodes)[number];
        lon: number;
        lat: number;
      }>;
      if (!validNodes.length) {
        return;
      }
      const fitKey = `${animation.case_id}|${selectedDay ?? "all"}|${nodes.length}`;
      if (lastFitKeyRef.current === fitKey) {
        return;
      }
      lastFitKeyRef.current = fitKey;
      const lats = validNodes.map((node) => node.lat);
      const lons = validNodes.map((node) => node.lon);
      const bounds = new TMap.LatLngBounds(
        new TMap.LatLng(Math.min(...lats), Math.min(...lons)),
        new TMap.LatLng(Math.max(...lats), Math.max(...lons)),
      );
      mapRef.current?.fitBounds(bounds, { padding: 70 });
    }, [animation, mapReady, selectedDay]);

    useImperativeHandle(
      ref,
      () => ({
        flyTo(lat: number, lon: number, day: number, stepIndex: number) {
          const TMap = window.TMap;
          const map = mapRef.current;
          if (!map || !TMap) {
            return;
          }
          const targetCenter = toLatLng(TMap, lon, lat);
          if (!targetCenter) {
            return;
          }

          const node = animation?.nodes.find(
            (item) => item.day === day && item.step_index === stepIndex,
          );
          const fill = node?.type_color || nodeTypeColor(node?.kind ?? "spot");

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
                position: targetCenter,
              },
            ]);
          }

          if (typeof map.easeTo === "function") {
            map.easeTo({
              center: targetCenter,
              duration: 700,
            });
            return;
          }

          const startCenter =
            typeof map.getCenter === "function" ? map.getCenter() : null;
          const startLat = startCenter ? startCenter.getLat() : lat;
          const startLon = startCenter ? startCenter.getLng() : lon;
          const targetLat = targetCenter.getLat();
          const targetLon = targetCenter.getLng();
          const duration = 700;
          const t0 = performance.now();
          const easeInOut = (t: number) =>
            t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
          const tick = (now: number) => {
            const p = easeInOut(Math.min((now - t0) / duration, 1));
            if (typeof map.setCenter === "function") {
              map.setCenter(
                new TMap.LatLng(
                  startLat + (targetLat - startLat) * p,
                  startLon + (targetLon - startLon) * p,
                ),
              );
            }
            if (p < 1) {
              requestAnimationFrame(tick);
            }
          };
          requestAnimationFrame(tick);
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

