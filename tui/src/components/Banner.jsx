import React, { useState, useEffect } from "react";
import { Box, Text } from "ink";

const LINES = [
  " ██████╗ ██╗   ██╗███████╗██████╗ ███████╗███████╗███████╗██████╗ ",
  "██╔═══██╗██║   ██║██╔════╝██╔══██╗██╔════╝██╔════╝██╔════╝██╔══██╗",
  "██║   ██║██║   ██║█████╗  ██████╔╝███████╗█████╗  █████╗  ██████╔╝",
  "██║   ██║╚██╗ ██╔╝██╔══╝  ██╔══██╗╚════██║██╔══╝  ██╔══╝  ██╔══██╗",
  "╚██████╔╝ ╚████╔╝ ███████╗██║  ██║███████║███████╗███████╗██║  ██║ ",
  " ╚═════╝   ╚═══╝  ╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝",
];

// Fade-in: dim orange → vivid orange → white flash → settle
const FRAMES = [
  ["#3a1800","#5a2a00","#7a3a00","#7a3a00","#5a2a00","#3a1800"],
  ["#7a3200","#b04800","#d06000","#d06000","#b04800","#7a3200"],
  ["#c05000","#e06c00","#ff8c00","#ff8c00","#e06c00","#c05000"],
  ["#d06800","#ff8800","#ffaa00","#ffaa00","#ff8800","#d06800"],
  ["#e06c00","#ff8c00","#ffcc44","#ffcc44","#ff8c00","#e06c00"],
  ["#cc6600","#ee8800","#ffbb33","#ffbb33","#ee8800","#cc6600"],
];

export function IntroBanner({ onDone }) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    if (frame < FRAMES.length - 1) {
      const t = setTimeout(() => setFrame((f) => f + 1), 120);
      return () => clearTimeout(t);
    } else {
      const t = setTimeout(onDone, 600);
      return () => clearTimeout(t);
    }
  }, [frame]);

  const colors = FRAMES[frame];
  return (
    <Box flexDirection="column" paddingX={2} paddingTop={2} paddingBottom={1}>
      {LINES.map((line, i) => (
        <Text key={i} color={colors[i]} bold>{line}</Text>
      ))}
    </Box>
  );
}

// Small sprite used as persistent header after intro
// ▛▀▜
// ▌⬡▐
// ▙▄▟
export function Sprite() {
  return (
    <Box flexDirection="column">
      <Text color="#e06c00" bold>{"▛▀▜"}</Text>
      <Text color="#e06c00" bold>{"▌⬡▐"}</Text>
      <Text color="#cc5500" bold>{"▙▄▟"}</Text>
    </Box>
  );
}
