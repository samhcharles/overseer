import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, Text, useInput } from "ink";
import {
  currentModelLabel,
  displayPath,
  formatRuntimeDescriptor,
  shortEndpoint,
} from "../runtime.js";

const WIDE_LAYOUT_MIN = 104;

// ── Eye color constants ────────────────────────────────────────────────────────
const LID   = "#c07030";  // upper eyelid
const LD2   = "#7d3a08";  // lower eyelid / shadow
const IRIS  = "#a85020";  // iris ring outline
const IRISF = "#8a3018";  // iris fill (between ring and pupil)
const PUPIL = "#0c0c0c";  // pupil
const WHITE = "#ffffff";  // sclera (solid block fill)
const GLINT = "#f0f0f0";  // pupil glint

// Row layout: 18 chars wide, almond shape
// Row 0: "   ╭──────────╮   "  (3+1+10+1+3 = 18)
// Row 1: " ╭─╯" + "██████████" + "╰─╮ "  (4+10+4 = 18)
// Row 2: " │" + "██" + "╭────────╮" + "██" + "│ "  (2+2+10+2+2 = 18)
// Row 3: " │" + "██" + "│" + [8 inner] + "│" + "██" + "│ "  (2+2+1+8+1+2+2 = 18)
//   inner center: "  " + "█▌██" + "  "  (2+4+2 = 8)
//   inner left:   "█▌██" + "    "       (4+4 = 8)
//   inner right:  "    " + "█▌██"       (4+4 = 8)
// Row 4: mirror of row 2 (LD2 sides)
// Row 5: mirror of row 1 (LD2 sides)
// Row 6: "   ╰──────────╯   "  (LD2)

const EYE_FRAMES = [
  // 0: OPEN_CENTER — eyeball white only flanks iris; lid body stays dark
  [
    [{ text: "   ╭──────────╮   ", color: LID }],
    [{ text: " ╭─╯          ╰─╮ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "╭────────╮", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "│", color: IRIS }, { text: "  ", color: IRISF }, { text: "█", color: PUPIL }, { text: "▌", color: GLINT }, { text: "██", color: PUPIL }, { text: "  ", color: IRISF }, { text: "│", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "╰────────╯", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LD2 }],
    [{ text: " ╰─╮          ╭─╯ ", color: LD2 }],
    [{ text: "   ╰──────────╯   ", color: LD2 }],
  ],
  // 1: OPEN_LEFT
  [
    [{ text: "   ╭──────────╮   ", color: LID }],
    [{ text: " ╭─╯          ╰─╮ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "╭────────╮", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "│", color: IRIS }, { text: "█", color: PUPIL }, { text: "▌", color: GLINT }, { text: "██", color: PUPIL }, { text: "    ", color: IRISF }, { text: "│", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "╰────────╯", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LD2 }],
    [{ text: " ╰─╮          ╭─╯ ", color: LD2 }],
    [{ text: "   ╰──────────╯   ", color: LD2 }],
  ],
  // 2: OPEN_RIGHT
  [
    [{ text: "   ╭──────────╮   ", color: LID }],
    [{ text: " ╭─╯          ╰─╮ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "╭────────╮", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "│", color: IRIS }, { text: "    ", color: IRISF }, { text: "█", color: PUPIL }, { text: "▌", color: GLINT }, { text: "██", color: PUPIL }, { text: "│", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LID }],
    [{ text: " │", color: LID }, { text: "██", color: WHITE }, { text: "╰────────╯", color: IRIS }, { text: "██", color: WHITE }, { text: "│ ", color: LD2 }],
    [{ text: " ╰─╮          ╭─╯ ", color: LD2 }],
    [{ text: "   ╰──────────╯   ", color: LD2 }],
  ],
  // 3: SQUINT — no sclera, lid closing
  [
    [{ text: "   ╭──────────╮   ", color: LID }],
    [{ text: " ╭─╯          ╰─╮ ", color: LID }],
    [{ text: " ╰──", color: LID }, { text: "──────────", color: LID }, { text: "──╯ ", color: LID }],
    [{ text: "    ", color: LID }, { text: "──────────", color: IRIS }, { text: "    ", color: LID }],
    [{ text: " ╭──", color: LD2 }, { text: "──────────", color: LD2 }, { text: "──╮ ", color: LD2 }],
    [{ text: " ╰─╮          ╭─╯ ", color: LD2 }],
    [{ text: "   ╰──────────╯   ", color: LD2 }],
  ],
  // 4: CLOSED
  [
    [{ text: "   ╭──────────╮   ", color: LID }],
    [{ text: " ╭─╯", color: LID }, { text: "          ", color: LID }, { text: "╰─╮ ", color: LID }],
    [{ text: " ╰──", color: LID }, { text: "──────────", color: LID }, { text: "──╯ ", color: LID }],
    [{ text: "    ", color: LD2 }, { text: "──────────", color: LD2 }, { text: "    ", color: LD2 }],
    [{ text: " ╭──", color: LD2 }, { text: "──────────", color: LD2 }, { text: "──╮ ", color: LD2 }],
    [{ text: " ╰─╮", color: LD2 }, { text: "          ", color: LD2 }, { text: "╭─╯ ", color: LD2 }],
    [{ text: "   ╰──────────╯   ", color: LD2 }],
  ],
];

// Double blink: rest → glance left → rest → glance right → rest → blink×2 → long rest
const EYE_STEPS = [
  { frame: 0, duration: 2200 },
  { frame: 1, duration: 150 },
  { frame: 0, duration: 400 },
  { frame: 0, duration: 1500 },
  { frame: 2, duration: 150 },
  { frame: 0, duration: 400 },
  { frame: 0, duration: 1200 },
  { frame: 3, duration: 80 },
  { frame: 4, duration: 80 },
  { frame: 3, duration: 60 },
  { frame: 0, duration: 220 },
  { frame: 3, duration: 60 },
  { frame: 4, duration: 70 },
  { frame: 3, duration: 60 },
  { frame: 0, duration: 2800 },
];

// ── Eye rendering helpers ──────────────────────────────────────────────────────

function useAnimatedFrame(steps) {
  const [sequenceIndex, setSequenceIndex] = useState(0);

  useEffect(() => {
    if (!steps.length) return undefined;
    const activeStep = steps[sequenceIndex] ?? steps[0];
    const timer = setTimeout(() => {
      setSequenceIndex((value) => (value + 1) % steps.length);
    }, activeStep.duration);
    return () => clearTimeout(timer);
  }, [sequenceIndex, steps]);

  return steps[sequenceIndex]?.frame ?? steps[0]?.frame ?? 0;
}

function EyeFrameRenderer({ frame }) {
  return (
    <Box flexDirection="column">
      {frame.map((row, rowIndex) => (
        <Box key={`eye-row-${rowIndex}`}>
          {row.map((seg, segIndex) => (
            <Text key={`eye-seg-${rowIndex}-${segIndex}`} color={seg.color} bold>
              {seg.text}
            </Text>
          ))}
        </Box>
      ))}
    </Box>
  );
}

function EyeArt() {
  const frameIndex = useAnimatedFrame(EYE_STEPS);
  const frame = EYE_FRAMES[frameIndex] ?? EYE_FRAMES[0];
  return <EyeFrameRenderer frame={frame} />;
}

function tintFrame(frame, lidColor, lid2Color) {
  return frame.map((row) =>
    row.map((seg) => {
      if (seg.color === LID) return { text: seg.text, color: lidColor };
      if (seg.color === LD2) return { text: seg.text, color: lid2Color };
      return seg;
    })
  );
}

// ── Norse glitch tagline ───────────────────────────────────────────────────────

const NORSE_TEXT = "Ek sé allt.";
const GLITCH_SET = "░▒▓╳│┤╡╢╣║╗╝┐└─┼╬═╫╪╩╦┘┌▄▀∅¤§";

function scramble(text, ratio) {
  return text
    .split("")
    .map((c) => {
      if (c === " " || c === ".") return c;
      if (Math.random() < ratio) {
        return GLITCH_SET[Math.floor(Math.random() * GLITCH_SET.length)];
      }
      return c;
    })
    .join("");
}

function GlitchText({ text, color }) {
  const [display, setDisplay] = useState(() => scramble(text, 0.95));
  const stepRef = useRef(0);
  const STEPS = 16;

  useEffect(() => {
    function tick() {
      stepRef.current += 1;
      if (stepRef.current >= STEPS) {
        setDisplay(text);
        return;
      }
      const ratio = 1 - stepRef.current / STEPS;
      setDisplay(scramble(text, ratio));
      setTimeout(tick, 75);
    }
    const id = setTimeout(tick, 120);
    return () => clearTimeout(id);
  }, [text]);

  return <Text color={color}>{display}</Text>;
}

// ── IntroBanner ───────────────────────────────────────────────────────────────

export function IntroBanner({ onDone, columns = 120, health = {}, sessionCount = 0, cwd = process.cwd() }) {
  const onDoneRef = useRef(onDone);
  const finishedRef = useRef(false);
  const wide = columns >= WIDE_LAYOUT_MIN;

  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useInput(() => {
    if (finishedRef.current) return;
    finishedRef.current = true;
    onDoneRef.current?.();
  });

  const endpoint = shortEndpoint(health?.api_url ?? health?.api_urls?.[0]);
  const backend = health?.backend_status === "ok" ? (health?.backend ?? "rotator") : "gateway";
  const model = currentModelLabel(null, health);
  const runtimeSummary = health?.backend_status === "ok"
    ? formatRuntimeDescriptor(backend, model)
    : "gateway warming up";

  const tips = [
    "Shift+Tab  →  cycle mode",
    "/          →  open command picker",
    "?          →  keyboard shortcuts",
    "Esc        →  stop a running request",
  ];

  return (
    <Box flexGrow={1} justifyContent="center" alignItems="center" paddingX={2}>
      <Box
        borderStyle="round"
        borderColor="#a85e14"
        paddingX={2}
        paddingY={1}
        width={Math.max(72, Math.min(columns - 4, 116))}
        flexDirection="column"
      >
        {wide ? (
          <Box justifyContent="space-between">
            {/* Left: eye + identity */}
            <Box flexDirection="column" flexGrow={1} paddingRight={4}>
              <EyeArt />
              <Box marginTop={1} flexDirection="column">
                <Text color="#d8d8d8" bold>I am Overseer.</Text>
                <GlitchText text={NORSE_TEXT} color="#e08a22" />
              </Box>
              <Box marginTop={1} flexDirection="column">
                <Text color="#666">{displayPath(cwd)}</Text>
                <Text color="#555">{`${runtimeSummary} · ${endpoint}`}</Text>
              </Box>
            </Box>

            {/* Right: tips */}
            <Box width={30} flexDirection="column">
              <Text color="#888" bold>GETTING STARTED</Text>
              <Text color="#444">──────────────────────────</Text>
              {tips.map((tip) => (
                <Text key={tip} color="#777">{tip}</Text>
              ))}
              <Box marginTop={1} flexDirection="column">
                <Text color="#d8d8d8" bold>MODES</Text>
                <Text color="#e06c00">[o] chat   read-only</Text>
                <Text color="#6366f1">[!] think  private</Text>
                <Text color="#10b981">[+] capture  writes</Text>
              </Box>
            </Box>
          </Box>
        ) : (
          <Box flexDirection="column">
            <EyeArt />
            <Box marginTop={1} flexDirection="column">
              <Text color="#d8d8d8" bold>I am Overseer.</Text>
              <GlitchText text={NORSE_TEXT} color="#e08a22" />
            </Box>
            <Box marginTop={1} flexDirection="column">
              <Text color="#666">{displayPath(cwd)}</Text>
              <Text color="#555">{`${runtimeSummary} · ${endpoint}`}</Text>
            </Box>
          </Box>
        )}

        <Box marginTop={1}>
          <Text color="#444">Press any key to continue.</Text>
        </Box>
      </Box>
    </Box>
  );
}

// ── Static sprite for shell header ────────────────────────────────────────────

export function Sprite({ accent = LID, dimAccent = LD2 }) {
  const tinted = tintFrame(EYE_FRAMES[0], accent, dimAccent);
  return <EyeFrameRenderer frame={tinted} />;
}
