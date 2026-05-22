import React, { useState, useEffect } from "react";
import { Box, Text } from "ink";

const SPINNER = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"];

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export function ThinkingIndicator({ label = "Processing…", modeAccent = "#e06c00", startedAt = null, tokenTotal = 0 }) {
  const [spinnerIndex, setSpinnerIndex] = useState(0);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const spinTimer = setInterval(() => setSpinnerIndex((i) => (i + 1) % SPINNER.length), 80);
    return () => clearInterval(spinTimer);
  }, []);

  useEffect(() => {
    if (!startedAt) return undefined;
    const elapsedTimer = setInterval(() => setElapsed(Date.now() - startedAt), 500);
    return () => clearInterval(elapsedTimer);
  }, [startedAt]);

  const spinner = SPINNER[spinnerIndex];
  const elapsedText = startedAt ? formatElapsed(elapsed) : null;
  const tokenText = tokenTotal > 0 ? `↓ ${tokenTotal.toLocaleString()} tokens` : null;
  const metaParts = [elapsedText, tokenText].filter(Boolean);
  const meta = metaParts.length ? `  (${metaParts.join(" · ")})` : "";

  return (
    <Box>
      <Text color={modeAccent} bold>{`${spinner}  ${label}`}</Text>
      {meta ? <Text color="#666">{meta}</Text> : null}
    </Box>
  );
}

// Legacy export kept for any future import compatibility
export function ThinkingDots() {
  return <ThinkingIndicator />;
}
