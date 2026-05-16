import type { SubtitleSegment, SubtitleStyle } from "../types";

const CJK_FONTS = [
  "Noto Sans CJK SC",
  "PingFang SC",
  "Microsoft YaHei",
  "SimHei",
  "sans-serif",
];

export interface SubtitleStylePanelProps {
  subtitle: SubtitleSegment;
  onStyleChange: (style: SubtitleStyle) => void;
}

/**
 * Panel for editing the active subtitle's style — font size, wrap width,
 * colors, and font family. The currently editable subtitle is determined by
 * the editor: it follows the playhead unless the user pins one explicitly.
 */
export function SubtitleStylePanel({ subtitle, onStyleChange }: SubtitleStylePanelProps) {
  const { style } = subtitle;

  function update(patch: Partial<SubtitleStyle>) {
    onStyleChange({ ...style, ...patch });
  }

  const approxPx = Math.round(style.fontSize * 1024);
  const wrapPct = Math.round((style.maxWidth ?? 0.5) * 100);

  return (
    <div>
      <div style={headerRowStyle}>
        <h3 style={{ margin: 0 }}>Subtitle style</h3>
        <span style={metaStyle}>
          {subtitle.startTime.toFixed(2)}s – {subtitle.endTime.toFixed(2)}s
        </span>
      </div>
      <p style={previewStyle}>“{subtitle.text}”</p>

      <div style={controlsStyle}>
        <Field label={`Size (${approxPx}px)`}>
          <input
            type="range"
            min={0.02}
            max={0.1}
            step={0.001}
            value={style.fontSize}
            onChange={(e) => update({ fontSize: parseFloat(e.target.value) })}
          />
        </Field>

        <Field label={`Wrap width (${wrapPct}%)`}>
          <input
            type="range"
            min={0.2}
            max={1}
            step={0.01}
            value={style.maxWidth ?? 0.5}
            onChange={(e) => update({ maxWidth: parseFloat(e.target.value) })}
          />
        </Field>

        <Field label="Alignment">
          <div style={alignGroupStyle} role="group" aria-label="Text alignment">
            {(["left", "center", "right"] as const).map((a) => {
              const active = (style.align ?? "center") === a;
              return (
                <button
                  key={a}
                  type="button"
                  className={`btn btn-sm ${active ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => update({ align: a })}
                  aria-pressed={active}
                  title={`Align ${a}`}
                  style={{ flex: 1 }}
                >
                  {a === "left" ? "⟸" : a === "center" ? "⇔" : "⟹"}
                </button>
              );
            })}
          </div>
        </Field>

        <Field label="Font color">
          <input
            type="color"
            value={style.fontColor}
            onChange={(e) => update({ fontColor: e.target.value })}
            style={colorInputStyle}
          />
        </Field>

        <Field label="Outline color">
          <input
            type="color"
            value={style.outlineColor}
            onChange={(e) => update({ outlineColor: e.target.value })}
            style={colorInputStyle}
          />
        </Field>

        <Field label="Font family">
          <select
            value={style.fontFamily}
            onChange={(e) => update({ fontFamily: e.target.value })}
          >
            {CJK_FONTS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={fieldStyle}>
      <span style={labelTextStyle}>{label}</span>
      {children}
    </div>
  );
}

const headerRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "baseline",
  gap: 12,
  marginBottom: 6,
  flexWrap: "wrap",
};

const metaStyle: React.CSSProperties = {
  color: "var(--text-muted)",
  fontSize: 12,
  fontVariantNumeric: "tabular-nums",
};

const previewStyle: React.CSSProperties = {
  margin: "0 0 12px",
  color: "var(--text)",
  fontSize: 13,
  fontStyle: "italic",
};

const controlsStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
  gap: 12,
};

const fieldStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const labelTextStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--text-muted)",
};

const colorInputStyle: React.CSSProperties = {
  width: "100%",
  height: 32,
  padding: 2,
  background: "var(--bg-elevated)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  cursor: "pointer",
};

const alignGroupStyle: React.CSSProperties = {
  display: "flex",
  gap: 4,
};
