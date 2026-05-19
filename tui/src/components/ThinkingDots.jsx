import React, { useState, useEffect } from "react";
import { Text } from "ink";

const FRAMES = [
  "  ·  ·  ·",
  "  ●  ·  ·",
  "  ●  ●  ·",
  "  ●  ●  ●",
  "  ·  ●  ●",
  "  ·  ·  ●",
];

export function ThinkingDots() {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setFrame((f) => (f + 1) % FRAMES.length), 120);
    return () => clearInterval(t);
  }, []);

  return (
    <Text color="#e06c00">
      {"⬡" + FRAMES[frame]}
    </Text>
  );
}
