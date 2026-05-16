import { Stage, Layer, Rect, Text, Image as KonvaImage, Group, Circle } from "react-konva";
import { useEffect, useRef, useState } from "react";
import type { SubtitleSegment } from "../types";
import type Konva from "konva";

/** Logical canvas dimensions (half of 1792×1024 source). The Stage is rendered
 *  at the actual container width but uses these as its internal coordinate
 *  space via Konva's scale prop, so position math is resolution-independent. */
export const CANVAS_WIDTH = 896;
export const CANVAS_HEIGHT = 512;
const ASPECT = CANVAS_WIDTH / CANVAS_HEIGHT;

const MIN_BOX_PX = 80;

export interface VideoCanvasProps {
  subtitles: SubtitleSegment[];
  currentTime: number;
  backgroundImage: string | null;
  selectedSubtitleId: string | null;
  onSubtitleSelect: (id: string) => void;
  onSubtitleMove: (id: string, position: { x: number; y: number }) => void;
  onSubtitleResize?: (id: string, maxWidth: number) => void;
}

/**
 * Clamp a normalized point so its bounding box stays inside [0,1] on both axes.
 * Exported for testing.
 */
export function clampPosition(
  x: number,
  y: number,
  boxWidth: number,
  boxHeight: number,
): { x: number; y: number } {
  const clampedX = Math.min(Math.max(x, 0), Math.max(1 - boxWidth, 0));
  const clampedY = Math.min(Math.max(y, 0), Math.max(1 - boxHeight, 0));
  return { x: clampedX, y: clampedY };
}

/**
 * Return subtitles visible at time T using half-open interval:
 * start_time <= T < end_time.
 * Exported for testing.
 */
export function getVisibleSubtitles(
  subtitles: SubtitleSegment[],
  time: number,
): SubtitleSegment[] {
  return subtitles.filter((s) => s.startTime <= time && time < s.endTime);
}

export function VideoCanvas({
  subtitles,
  currentTime,
  backgroundImage,
  selectedSubtitleId,
  onSubtitleSelect,
  onSubtitleMove,
  onSubtitleResize,
}: VideoCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [bgImg, setBgImg] = useState<HTMLImageElement | null>(null);
  const [size, setSize] = useState({ w: CANVAS_WIDTH, h: CANVAS_HEIGHT });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const w = el.clientWidth;
      if (w > 0) setSize({ w, h: w / ASPECT });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!backgroundImage) {
      setBgImg(null);
      return;
    }
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.src = backgroundImage;
    img.onload = () => setBgImg(img);
    img.onerror = () => setBgImg(null);
  }, [backgroundImage]);

  const visible = getVisibleSubtitles(subtitles, currentTime);
  const scale = size.w / CANVAS_WIDTH;

  return (
    <div ref={containerRef} style={{ width: "100%", aspectRatio: `${ASPECT}` }}>
      <Stage width={size.w} height={size.h} scaleX={scale} scaleY={scale}>
        <Layer>
          {bgImg ? (
            <KonvaImage image={bgImg} width={CANVAS_WIDTH} height={CANVAS_HEIGHT} />
          ) : (
            <Rect width={CANVAS_WIDTH} height={CANVAS_HEIGHT} fill="black" />
          )}

          {visible.map((sub) => (
            <SubtitleNode
              key={sub.id}
              subtitle={sub}
              isSelected={sub.id === selectedSubtitleId}
              onSelect={onSubtitleSelect}
              onMove={onSubtitleMove}
              onResize={onSubtitleResize}
            />
          ))}
        </Layer>
      </Stage>
    </div>
  );
}

/* ---- Subtitle node sub-component ---- */

interface SubtitleNodeProps {
  subtitle: SubtitleSegment;
  isSelected: boolean;
  onSelect: (id: string) => void;
  onMove: (id: string, position: { x: number; y: number }) => void;
  onResize?: (id: string, maxWidth: number) => void;
}

function SubtitleNode({ subtitle, isSelected, onSelect, onMove, onResize }: SubtitleNodeProps) {
  const groupRef = useRef<Konva.Group>(null);
  const handleRef = useRef<Konva.Circle>(null);
  const fillTextRef = useRef<Konva.Text>(null);
  const [measuredHeight, setMeasuredHeight] = useState(0);

  const fontSize = Math.round(subtitle.style.fontSize * CANVAS_HEIGHT);
  const maxWidthPx = Math.max(MIN_BOX_PX, subtitle.style.maxWidth * CANVAS_WIDTH);
  // Backend stamps the outline as size = font_size / 16. Mirror that.
  const outlinePx = Math.max(1, Math.round(fontSize / 16));

  const centerX = subtitle.position.x * CANVAS_WIDTH;
  const centerY = subtitle.position.y * CANVAS_HEIGHT;
  const boxHeight = Math.max(measuredHeight, fontSize);

  useEffect(() => {
    const node = fillTextRef.current;
    if (!node) return;
    setMeasuredHeight(node.height());
  }, [
    subtitle.text,
    subtitle.style.fontSize,
    subtitle.style.fontFamily,
    subtitle.style.maxWidth,
  ]);

  function handleGroupDragMove(e: Konva.KonvaEventObject<DragEvent>) {
    // Only react to drags of the group itself, not bubbled child events.
    if (e.target !== e.currentTarget) return;
    const g = groupRef.current;
    const h = handleRef.current;
    if (!g || !h) return;
    // Keep the handle locked to the right edge of the box during drag.
    h.x(g.x() + maxWidthPx / 2);
    h.y(g.y());
  }

  function handleGroupDragEnd(e: Konva.KonvaEventObject<DragEvent>) {
    if (e.target !== e.currentTarget) return;
    const node = e.target;
    const halfW = maxWidthPx / 2;
    const halfH = boxHeight / 2;
    const minX = halfW;
    const maxX = CANVAS_WIDTH - halfW;
    const minY = halfH;
    const maxY = CANVAS_HEIGHT - halfH;
    const newX = Math.min(Math.max(node.x(), minX), maxX);
    const newY = Math.min(Math.max(node.y(), minY), maxY);
    node.x(newX);
    node.y(newY);
    // Snap the handle to the clamped right edge.
    const h = handleRef.current;
    if (h) {
      h.x(newX + maxWidthPx / 2);
      h.y(newY);
    }
    onMove(subtitle.id, { x: newX / CANVAS_WIDTH, y: newY / CANVAS_HEIGHT });
  }

  function handleResizeDrag(e: Konva.KonvaEventObject<DragEvent>) {
    if (!onResize) return;
    e.cancelBubble = true;
    const node = e.target;
    const g = groupRef.current;
    // Use the group's *live* x/y (during a drag the prop hasn't updated yet)
    // so resize math is correct even mid-move.
    const liveCenterX = g ? g.x() : centerX;
    const liveCenterY = g ? g.y() : centerY;
    const handleAbsX = node.x();
    const newHalfWidth = handleAbsX - liveCenterX;
    const newWidthPx = Math.max(MIN_BOX_PX, Math.min(CANVAS_WIDTH * 0.98, newHalfWidth * 2));
    const newMaxWidth = Math.min(1, newWidthPx / CANVAS_WIDTH);
    // Lock the handle to the right edge of the new width.
    node.x(liveCenterX + newWidthPx / 2);
    node.y(liveCenterY);
    onResize(subtitle.id, newMaxWidth);
  }

  const boxStroke = isSelected ? "rgba(124, 58, 237, 0.9)" : "transparent";

  return (
    <>
      <Group
        ref={groupRef}
        x={centerX}
        y={centerY}
        draggable
        onClick={() => onSelect(subtitle.id)}
        onTap={() => onSelect(subtitle.id)}
        onDragMove={handleGroupDragMove}
        onDragEnd={handleGroupDragEnd}
      >
        <Rect
          x={-maxWidthPx / 2}
          y={-boxHeight / 2}
          width={maxWidthPx}
          height={boxHeight}
          fill="rgba(0,0,0,0.001)"
          stroke={boxStroke}
          strokeWidth={1}
          dash={[6, 6]}
        />

        <Text
          text={subtitle.text}
          x={-maxWidthPx / 2}
          y={-boxHeight / 2}
          width={maxWidthPx}
          align={subtitle.style.align ?? "center"}
          fontSize={fontSize}
          fontFamily={subtitle.style.fontFamily}
          stroke={subtitle.style.outlineColor}
          strokeWidth={outlinePx * 2}
          fillEnabled={false}
          lineJoin="round"
          listening={false}
        />

        <Text
          ref={fillTextRef}
          text={subtitle.text}
          x={-maxWidthPx / 2}
          y={-boxHeight / 2}
          width={maxWidthPx}
          align={subtitle.style.align ?? "center"}
          fontSize={fontSize}
          fontFamily={subtitle.style.fontFamily}
          fill={subtitle.style.fontColor}
          listening={false}
        />
      </Group>

      {/* Sibling resize handle — its drag events don't bubble to the group. */}
      {isSelected && onResize && (
        <Circle
          ref={handleRef}
          x={centerX + maxWidthPx / 2}
          y={centerY}
          radius={8}
          fill="rgba(124, 58, 237, 1)"
          stroke="#fff"
          strokeWidth={2}
          draggable
          onMouseDown={(e) => (e.cancelBubble = true)}
          onTouchStart={(e) => (e.cancelBubble = true)}
          onDragStart={(e) => (e.cancelBubble = true)}
          onDragMove={handleResizeDrag}
          onDragEnd={handleResizeDrag}
        />
      )}
    </>
  );
}
